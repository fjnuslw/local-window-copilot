from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.schemas.analyze import WindowAnalysis
from app.schemas.memory import MemoryItem, MemorySnapshot
from app.schemas.observation import ObservationCard
from app.services.runtime_store import RuntimeStore, get_runtime_store


WORKING_OBSERVATION_KEY = "memory:working:observation"
MEMORY_ITEMS_KEY = "memory:items"
DEFAULT_MAX_ITEMS = 40


class MemoryService:
    def __init__(
        self,
        *,
        runtime_store: RuntimeStore,
        max_items: int = DEFAULT_MAX_ITEMS,
    ) -> None:
        self.runtime_store = runtime_store
        self.max_items = max_items

    def save_observation(self, observation: ObservationCard) -> None:
        if observation.privacy_state == "privacy":
            self.runtime_store.delete(WORKING_OBSERVATION_KEY)
            return
        payload = observation.model_dump(mode="json")
        self.runtime_store.set_json(WORKING_OBSERVATION_KEY, payload)
        self.runtime_store.record_event("memory:working_observation", payload)

    def remember_analysis(
        self,
        *,
        observation: ObservationCard,
        analysis: WindowAnalysis,
        latency_ms: int | None = None,
    ) -> MemoryItem | None:
        if observation.privacy_state == "privacy":
            return None
        key_points = "；".join(analysis.key_points[:4])
        text = analysis.summary
        if key_points:
            text = f"{text} 关键点：{key_points}"
        item = MemoryItem(
            scope="session",
            kind="analysis_summary",
            text=text,
            tags=self._observation_tags(observation, extra=[analysis.window_type, "summary"]),
            confidence=0.85,
            source_observation_id=observation.observation_id,
            metadata={
                "window_type": analysis.window_type,
                "latency_ms": latency_ms,
                "privacy_state": observation.privacy_state,
            },
        )
        self._append_item(item)
        return item

    def remember_user_question(
        self,
        *,
        question: str,
        observation_id: str | None = None,
        tags: list[str] | None = None,
    ) -> MemoryItem:
        item = MemoryItem(
            scope="session",
            kind="user_question",
            text=question.strip(),
            tags=tags or ["question"],
            confidence=1.0,
            source_observation_id=observation_id,
        )
        self._append_item(item)
        return item

    def remember_assistant_answer(
        self,
        *,
        answer: str,
        observation_id: str | None = None,
        tags: list[str] | None = None,
    ) -> MemoryItem:
        item = MemoryItem(
            scope="session",
            kind="assistant_answer",
            text=answer.strip(),
            tags=tags or ["answer"],
            confidence=0.75,
            source_observation_id=observation_id,
        )
        self._append_item(item)
        return item

    def remember_note(
        self,
        *,
        note: str,
        observation_id: str | None = None,
        tags: list[str] | None = None,
    ) -> MemoryItem:
        item = MemoryItem(
            scope="session",
            kind="user_note",
            text=note.strip(),
            tags=tags or ["note"],
            confidence=0.95,
            source_observation_id=observation_id,
        )
        self._append_item(item)
        return item

    def retrieve_for_observation(
        self,
        observation: ObservationCard | None,
        *,
        question: str | None = None,
        limit: int = 5,
    ) -> list[MemoryItem]:
        items = self._load_items()
        if not items:
            return []
        if observation is None and not question:
            return items[-limit:]

        query_terms = self._query_terms(observation, question)
        scored: list[tuple[int, MemoryItem]] = []
        for index, item in enumerate(items):
            score = self._score_item(item, query_terms)
            if observation is not None and item.source_observation_id == observation.observation_id:
                score += 3
            score += min(index, 10)
            if score > 0:
                scored.append((score, item))

        scored.sort(key=lambda row: row[0], reverse=True)
        return [item for _score, item in scored[:limit]]

    def get_snapshot(
        self,
        *,
        observation: ObservationCard | None = None,
        question: str | None = None,
        limit: int = 5,
    ) -> MemorySnapshot:
        working_observation = observation or self._load_working_observation()
        return MemorySnapshot(
            working_observation=working_observation,
            relevant_items=self.retrieve_for_observation(
                working_observation,
                question=question,
                limit=limit,
            ),
        )

    def _append_item(self, item: MemoryItem) -> None:
        items = self._load_items()
        items.append(item)
        items = items[-self.max_items :]
        payload = [stored_item.model_dump(mode="json") for stored_item in items]
        self.runtime_store.set_json(MEMORY_ITEMS_KEY, payload)
        self.runtime_store.record_event("memory:item", item.model_dump(mode="json"))

    def _load_items(self) -> list[MemoryItem]:
        data = self.runtime_store.get_json(MEMORY_ITEMS_KEY)
        if not isinstance(data, list):
            return []
        items: list[MemoryItem] = []
        for raw_item in data:
            if isinstance(raw_item, dict):
                items.append(MemoryItem.model_validate(raw_item))
        return items

    def _load_working_observation(self) -> ObservationCard | None:
        data = self.runtime_store.get_json(WORKING_OBSERVATION_KEY)
        if not isinstance(data, dict):
            return None
        return ObservationCard.model_validate(data)

    @staticmethod
    def _observation_tags(
        observation: ObservationCard,
        *,
        extra: list[str] | None = None,
    ) -> list[str]:
        tags = [
            tag
            for tag in (
                observation.app_name,
                observation.window_kind_hint,
                observation.privacy_state,
            )
            if tag
        ]
        tags.extend(extra or [])
        return list(dict.fromkeys(tags))

    @staticmethod
    def _query_terms(observation: ObservationCard | None, question: str | None) -> set[str]:
        values: list[str] = []
        if observation is not None:
            values.extend(
                [
                    observation.app_name or "",
                    observation.window_title,
                    observation.window_kind_hint,
                    observation.privacy_state,
                ]
            )
        if question:
            values.append(question)
        terms: set[str] = set()
        for value in values:
            for token in value.replace("-", " ").replace("_", " ").split():
                token = token.strip().lower()
                if len(token) >= 2:
                    terms.add(token)
        return terms

    @staticmethod
    def _score_item(item: MemoryItem, query_terms: set[str]) -> int:
        if not query_terms:
            return 1
        searchable = " ".join([item.text, *item.tags]).lower()
        return sum(1 for term in query_terms if term in searchable)


@lru_cache
def get_memory_service() -> MemoryService:
    return MemoryService(
        runtime_store=get_runtime_store(),
        max_items=get_settings().memory_max_items,
    )
