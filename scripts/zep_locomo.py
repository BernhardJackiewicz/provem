"""Zep arm for the LoCoMo head-to-head — configured per Zep's OWN checklist.

Zep publicly criticized Mem0's evaluation of Zep on three points (blog:
"Is Mem0 Really SOTA in Agent Memory?"). We follow their prescription exactly,
and document it, so the "you misconfigured us" counter is pre-empted:
  1. Proper user model: ONE graph owner per conversation (speaker A = role
     "user"), the other participant as role "assistant"; both carry `name`.
  2. Timestamps via the dedicated `created_at` field on every message (NOT
     appended to the text, which Zep explicitly called out as wrong).
  3. Retrieval via PARALLEL graph searches (edges + nodes), composed into a
     context block of dated facts + entity summaries, capped at the same
     k-budget the other systems got.

Everything downstream is identical to the Mem0 arm: neutral answerer prompt
(pv=v1) with the empty-content fix, strict gpt-5 judge (cached), and the same
row format so the existing pairing/report/second-judge tooling applies.
Keys from env only (ZEP_API_KEY, OPENAI_API_KEY).
"""
import argparse
import concurrent.futures
import importlib.util
import json
import os
import sys
import threading
import time

sys.path.insert(0, "src")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


e2e = _load("e2e", os.path.join(os.path.dirname(__file__), "mem0_locomo_e2e.py"))
from cognitive_memory.locomo_eval import LoCoMoLoader


def zep_client():
    from zep_cloud.client import Zep
    return Zep(api_key=os.environ["ZEP_API_KEY"])


def _sessions(sample):
    """Group episodes by session prefix of dia_id ('D<sess>:<turn>')."""
    sessions = {}
    for ep in sample.episodes:
        sid = str(getattr(ep, "id", "") or "").split(":")[0]
        sessions.setdefault(sid, []).append(ep)
    return sessions


def _speaker_and_text(content):
    if ": " in content:
        speaker, text = content.split(": ", 1)
        return speaker.strip(), text.strip()
    return "unknown", content


def ingest(client, sample, ci, uid_prefix):
    from zep_cloud import Message

    user_id = "%s_conv%d" % (uid_prefix, ci)
    try:
        client.user.add(user_id=user_id)
    except Exception:
        pass  # already exists
    # graph owner = first speaker of the conversation (Zep checklist point 1)
    owner, _ = _speaker_and_text(sample.episodes[0].content)
    # idempotent resume via a LOCAL state file (thread.get does not expose
    # messages, so remote checks are unreliable)
    state_path = os.path.join(os.environ.get("ZEP_STATE_DIR", "/tmp"), "zep_ingested.json")
    try:
        state = set(json.load(open(state_path)))
    except Exception:
        state = set()
    added = 0
    for sid, eps in sorted(_sessions(sample).items()):
        thread_id = "%s_%s" % (user_id, sid.lower().replace(":", "_"))
        if thread_id in state:
            continue
        try:
            client.thread.create(thread_id=thread_id, user_id=user_id)
        except Exception:
            pass
        batch = []
        for ep in eps:
            speaker, text = _speaker_and_text(ep.content)
            role = "user" if speaker == owner else "assistant"
            ts = getattr(ep, "timestamp", None)
            batch.append(Message(
                role=role, name=speaker, content=text,
                created_at=(ts.isoformat() if ts.tzinfo else ts.isoformat() + "Z") if ts is not None else None,
            ))
        # add in chunks; NEVER swallow a failed chunk (a silent-exhaustion bug
        # here once dropped an entire ingest while looking successful)
        for start in range(0, len(batch), 10):
            chunk = batch[start:start + 10]
            ok = False
            for attempt in range(10):
                try:
                    client.thread.add_messages(thread_id, messages=chunk)
                    added += len(chunk)
                    ok = True
                    break
                except Exception as err:
                    msg = str(err)
                    # only real 429s are retryable; the error text ALWAYS contains
                    # x-ratelimit headers, so never match on the word "rate"
                    if "status_code: 429" in msg:
                        time.sleep(min(3 * 2 ** attempt, 120)); continue
                    raise
            if not ok:
                raise RuntimeError("chunk failed after 10 rate-limit retries (%s %s)" % (thread_id, start))
            time.sleep(2.0)  # pace below the trial ingestion quota instead of slamming it
        # verify persistence before recording the session as done
        check = client.thread.get(thread_id, lastn=1)
        if not (getattr(check, "messages", None) or []):
            raise RuntimeError("VERIFY FAILED: %s reads back empty after add" % thread_id)
        print("    %s: %d msgs (verified)" % (sid, len(batch)), flush=True)
        state.add(thread_id)
        try:
            json.dump(sorted(state), open(state_path, "w"))
        except Exception:
            pass
    return user_id, owner, added


def zep_context(client, user_id, question, k=20):
    """Parallel edge+node searches (Zep checklist point 3), composed context."""
    results = {}

    def search(scope, limit):
        try:
            r = client.graph.search(user_id=user_id, query=question, scope=scope,
                                    limit=limit, reranker="rrf")
            results[scope] = r
        except Exception as err:
            results[scope] = err

    threads = [threading.Thread(target=search, args=("edges", k)),
               threading.Thread(target=search, args=("nodes", max(k // 2, 5)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if isinstance(results.get("edges"), Exception) and isinstance(results.get("nodes"), Exception):
        return "__ERR__" + str(results["edges"])[:160]

    lines = []
    edges = getattr(results.get("edges"), "edges", None) or []
    for e in edges[:k]:
        fact = getattr(e, "fact", None)
        if not fact:
            continue
        valid = getattr(e, "valid_at", None)
        invalid = getattr(e, "invalid_at", None)
        span = ""
        if valid or invalid:
            span = " (%s - %s)" % (valid or "unknown", invalid or "present")
        lines.append("FACT: %s%s" % (fact, span))
    nodes = getattr(results.get("nodes"), "nodes", None) or []
    for n in nodes[:max(k // 2, 5)]:
        name = getattr(n, "name", "")
        summary = getattr(n, "summary", "") or ""
        if name or summary:
            lines.append("ENTITY %s: %s" % (name, summary[:300]))
    return " \n ".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["ingest", "eval", "probe"], required=True)
    ap.add_argument("--convs", default=None)
    ap.add_argument("--uid-prefix", default="provem_zep")
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit-qa", type=int, default=None)
    ap.add_argument("--out", default="docs/runs/zep_locomo.jsonl")
    ap.add_argument("--cache-dir", default="/private/tmp/claude-501/-Users-bernhard-Desktop-brain/e9f97b9f-09f0-4aff-ac11-a991e6b1aafa/scratchpad")
    args = ap.parse_args()

    e2e._ANSWER_CACHE = e2e.DiskCache(os.path.join(args.cache_dir, "llm_cache_answer.jsonl"))
    e2e._JUDGE_CACHE = e2e.DiskCache(os.path.join(args.cache_dir, "llm_cache_judge.jsonl"))

    client = zep_client()
    samples = LoCoMoLoader().load("data/external/locomo/locomo10.json")
    idxs = [int(x) for x in args.convs.split(",")] if args.convs else list(range(len(samples)))

    if args.mode == "ingest":
        for ci in idxs:
            user_id, owner, added = ingest(client, samples[ci], ci, args.uid_prefix)
            print("conv%d: user=%s owner=%s messages=%d" % (ci, user_id, owner, added), flush=True)
        print("INGEST FIRED (Zep builds the graph asynchronously)", flush=True)
        return

    if args.mode == "probe":
        for ci in idxs[:1]:
            uid = "%s_conv%d" % (args.uid_prefix, ci)
            q = [q for q in samples[ci].questions if not q.expected_abstain][0]
            ctx = zep_context(client, uid, q.question, args.top_k)
            print("Q:", q.question[:80])
            print("CTX (%d chars):" % len(ctx), ctx[:500])
        return

    # eval
    done = set()
    if os.path.exists(args.out):
        for line in open(args.out):
            try:
                r = json.loads(line)
                done.add((r["conv"], r["qid"]))
            except Exception:
                pass
    out = open(args.out, "a")
    lock = threading.Lock()
    tally = {"ans": [0, 0], "abst": [0, 0], "err": 0}

    def work(task):
        ci, q = task
        uid = "%s_conv%d" % (args.uid_prefix, ci)
        ctx = zep_context(client, uid, q.question, args.top_k)
        if ctx.startswith("__ERR__"):
            with lock:
                tally["err"] += 1
            return
        pred = e2e.answerer("gpt-5-mini", q.question, ctx, effort="medium", qid=q.question_id)
        if q.expected_abstain:
            correct = e2e.is_noanswer(pred)
        else:
            correct = (not e2e.is_noanswer(pred)) and e2e.judge("gpt-5", q.question, q.answers, pred,
                                                               effort="low", qid=q.question_id)
        row = {"conv": ci, "qid": q.question_id, "system": "zep", "category": q.category,
               "abstain": q.expected_abstain, "predicted": pred, "correct": bool(correct)}
        with lock:
            out.write(json.dumps(row) + "\n"); out.flush()
            key = "abst" if q.expected_abstain else "ans"
            tally[key][1] += 1; tally[key][0] += 1 if correct else 0
            n = tally["ans"][1] + tally["abst"][1]
            if n % 50 == 0:
                print("  ...%d done (EUR %.2f)" % (n, e2e._COST["usd"] * e2e.EUR_PER_USD), flush=True)

    tasks = []
    qa = 0
    for ci in idxs:
        for q in samples[ci].questions:
            if (ci, q.question_id) in done:
                continue
            tasks.append((ci, q))
            qa += 1
            if args.limit_qa and qa >= args.limit_qa:
                break
        if args.limit_qa and qa >= args.limit_qa:
            break
    print("zep eval: %d tasks" % len(tasks), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, tasks))
    out.close()
    if tally["ans"][1]:
        print("ZEP answerable: %.3f (%d/%d)" % (tally["ans"][0] / tally["ans"][1], *tally["ans"]))
    if tally["abst"][1]:
        print("ZEP abstain: %.3f (%d/%d)" % (tally["abst"][0] / tally["abst"][1], *tally["abst"]))
    print("errors: %d | openai cost: EUR %.2f" % (tally["err"], e2e._COST["usd"] * e2e.EUR_PER_USD))


if __name__ == "__main__":
    main()
