from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .models import ConsolidationRun, Episode, MemoryCandidate, MemoryEvent, Reflection, TemporalFact


class InMemoryStore:
    """Simple repository ports for the prototype.

    The class intentionally keeps separate collections for episodes, facts,
    candidates and reflections. Production adapters can map these ports to
    Postgres, Graphiti/Neo4j, Letta/MemFS or another backing service.
    """

    def __init__(self) -> None:
        self.episodes: Dict[str, Episode] = {}
        self.candidates: Dict[str, MemoryCandidate] = {}
        self.facts: Dict[str, TemporalFact] = {}
        self.events: Dict[str, MemoryEvent] = {}
        self.reflections: Dict[str, Reflection] = {}
        self.consolidation_runs: Dict[str, ConsolidationRun] = {}
        self.audit_log: List[Dict[str, str]] = []
        self.retrieval_traces: List[Dict[str, object]] = []

    def add_episode(self, episode: Episode) -> Episode:
        self.episodes[episode.id] = episode
        self.audit("episode_added", episode.id)
        return episode

    def add_candidate(self, candidate: MemoryCandidate) -> MemoryCandidate:
        self.candidates[candidate.id] = candidate
        self.audit("candidate_added", candidate.id)
        return candidate

    def add_fact(self, fact: TemporalFact) -> TemporalFact:
        self.facts[fact.id] = fact
        self.audit("fact_added", fact.id)
        return fact

    def update_fact(self, fact: TemporalFact) -> TemporalFact:
        self.facts[fact.id] = fact
        self.audit("fact_updated", fact.id)
        return fact

    def add_event(self, event: MemoryEvent) -> MemoryEvent:
        self.events[event.id] = event
        self.audit("event_added", event.id)
        return event

    def update_event(self, event: MemoryEvent) -> MemoryEvent:
        self.events[event.id] = event
        self.audit("event_updated", event.id)
        return event

    def add_reflection(self, reflection: Reflection) -> Reflection:
        self.reflections[reflection.id] = reflection
        self.audit("reflection_added", reflection.id)
        return reflection

    def update_reflection(self, reflection: Reflection) -> Reflection:
        self.reflections[reflection.id] = reflection
        self.audit("reflection_updated", reflection.id)
        return reflection

    def add_consolidation_run(self, run: ConsolidationRun) -> ConsolidationRun:
        self.consolidation_runs[run.id] = run
        self.audit("consolidation_run_added", run.id)
        return run

    def get_episode(self, episode_id: str) -> Optional[Episode]:
        return self.episodes.get(episode_id)

    def get_fact(self, fact_id: str) -> Optional[TemporalFact]:
        return self.facts.get(fact_id)

    def get_event(self, event_id: str) -> Optional[MemoryEvent]:
        return self.events.get(event_id)

    def get_reflection(self, reflection_id: str) -> Optional[Reflection]:
        return self.reflections.get(reflection_id)

    def get_consolidation_run(self, run_id: str) -> Optional[ConsolidationRun]:
        return self.consolidation_runs.get(run_id)

    def list_episodes(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[Episode]:
        episodes = list(self.episodes.values())
        if user_id is not None:
            episodes = [episode for episode in episodes if episode.user_id == user_id]
        if project_id is not None:
            episodes = [episode for episode in episodes if episode.project_id == project_id]
        return sorted(episodes, key=lambda episode: episode.timestamp)

    def list_facts(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[TemporalFact]:
        facts = list(self.facts.values())
        if user_id is not None:
            facts = [fact for fact in facts if fact.user_id == user_id]
        if project_id is not None:
            facts = [fact for fact in facts if fact.project_id == project_id]
        return sorted(facts, key=lambda fact: fact.valid_at)

    def list_events(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[MemoryEvent]:
        events = list(self.events.values())
        if user_id is not None:
            events = [event for event in events if event.context.user_id == user_id]
        if project_id is not None:
            events = [event for event in events if event.context.project_id == project_id]
        return sorted(events, key=lambda event: event.timestamp)

    def list_reflections(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[Reflection]:
        reflections = list(self.reflections.values())
        if user_id is not None:
            reflections = [reflection for reflection in reflections if reflection.user_id == user_id]
        if project_id is not None:
            reflections = [reflection for reflection in reflections if reflection.project_id == project_id]
        return sorted(reflections, key=lambda reflection: reflection.created_at)

    def active_facts(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[TemporalFact]:
        return [
            fact
            for fact in self.list_facts(user_id=user_id, project_id=project_id)
            if fact.is_active()
        ]

    def active_reflections(self, user_id: Optional[str] = None, project_id: Optional[str] = None) -> List[Reflection]:
        return [
            reflection
            for reflection in self.list_reflections(user_id=user_id, project_id=project_id)
            if reflection.is_active()
        ]

    def list_consolidation_runs(
        self,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> List[ConsolidationRun]:
        runs = list(self.consolidation_runs.values())
        if user_id is not None:
            runs = [run for run in runs if run.user_id == user_id]
        if project_id is not None:
            runs = [run for run in runs if run.project_id == project_id]
        return sorted(runs, key=lambda run: run.created_at)

    def matching_active_facts(
        self,
        subject: str,
        relation: str,
        user_id: str,
        project_id: str,
    ) -> List[TemporalFact]:
        return [
            fact
            for fact in self.active_facts(user_id=user_id, project_id=project_id)
            if fact.subject == subject and fact.relation == relation
        ]

    def all_memory_items(self) -> Iterable[object]:
        for fact in self.facts.values():
            yield fact
        for event in self.events.values():
            yield event
        for reflection in self.reflections.values():
            yield reflection

    def audit(self, event: str, target_id: str) -> None:
        self.audit_log.append({"event": event, "target_id": target_id})

    def add_retrieval_trace(self, trace: Dict[str, object]) -> None:
        self.retrieval_traces.append(dict(trace))
