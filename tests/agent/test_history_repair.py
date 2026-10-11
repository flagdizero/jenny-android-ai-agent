"""Test per le riparazioni di history in ``jenny.agent.history_repair``.

Copre ognuna delle quattro funzioni del modulo (drop degli orfani, backfill dei
tool_use non soddisfatti, microcompact dei vecchi risultati tool, taglio del
prefisso orfano), sia in casi già-sani (no-op, identità preservata) sia in
casi rotti che richiedono la riparazione.
"""

from __future__ import annotations

from jenny.agent.history_repair import (
    BACKFILL_CONTENT,
    COMPACTABLE_TOOLS,
    MICROCOMPACT_BATCH,
    MICROCOMPACT_KEEP_RECENT,
    MICROCOMPACT_MIN_CHARS,
    backfill_missing_tool_results,
    drop_orphan_tool_results,
    microcompact,
)
from jenny.utils.helpers import maybe_persist_tool_result


def _assistant(call_id: str, name: str = "read_file") -> dict:
    return {
        "role": "assistant",
        "tool_calls": [{"id": call_id, "function": {"name": name}}],
    }


def _tool(call_id: str, content: str = "result", name: str | None = None) -> dict:
    msg = {"role": "tool", "tool_call_id": call_id, "content": content}
    if name is not None:
        msg["name"] = name
    return msg


class TestDropOrphanToolResults:
    """Drop dei tool result senza tool_call corrispondente prima."""

    def test_healthy_history_is_noop_and_returns_same_object(self):
        messages = [
            {"role": "user", "content": "hi"},
            _assistant("1"),
            _tool("1"),
        ]
        result = drop_orphan_tool_results(messages)
        assert result is messages

    def test_drops_leading_orphan_tool_result(self):
        messages = [
            {"role": "user", "content": "hi"},
            _tool("orphan"),
            {"role": "assistant", "content": "ok"},
        ]
        original = [dict(m) for m in messages]
        result = drop_orphan_tool_results(messages)
        assert result == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok"},
        ]
        # L'input non viene mutato.
        assert messages == original

    def test_result_contains_copies_not_same_dict_objects(self):
        messages = [
            {"role": "user", "content": "hi"},
            _tool("orphan"),
        ]
        result = drop_orphan_tool_results(messages)
        assert result[0] == messages[0]
        assert result[0] is not messages[0]

    def test_drops_orphan_in_the_middle_keeps_valid_pair(self):
        messages = [
            _assistant("1"),
            _tool("orphan-mid"),  # nessun tool_call "orphan-mid" dichiarato
            _tool("1"),
        ]
        result = drop_orphan_tool_results(messages)
        assert result == [_assistant("1"), _tool("1")]

    def test_declared_id_stays_declared_for_later_duplicate_tool_results(self):
        """Comportamento reale (non consuma l'id): un secondo tool result con lo
        stesso tool_call_id non viene considerato orfano, anche se il primo lo
        ha già "usato"."""
        messages = [
            _assistant("1"),
            _tool("1", content="first"),
            _tool("1", content="second-duplicate"),
        ]
        result = drop_orphan_tool_results(messages)
        assert result is messages  # nessun drop: entrambi i "1" restano legali

    def test_no_orphans_multiple_valid_pairs_is_noop(self):
        messages = [
            _assistant("a"),
            _tool("a"),
            _assistant("b"),
            _tool("b"),
        ]
        assert drop_orphan_tool_results(messages) is messages


class TestBackfillMissingToolResults:
    """Inserimento di risultati sintetici per tool_use orfani."""

    def test_all_fulfilled_is_noop_and_returns_same_object(self):
        messages = [_assistant("1"), _tool("1")]
        assert backfill_missing_tool_results(messages) is messages

    def test_missing_single_tool_call_inserted_right_after_assistant(self):
        messages = [
            _assistant("c1", name="read_file"),
            {"role": "user", "content": "next"},
        ]
        result = backfill_missing_tool_results(messages)
        assert result[0] == messages[0]
        assert result[1] == {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "read_file",
            "content": BACKFILL_CONTENT,
        }
        assert result[2] == messages[1]

    def test_missing_tool_call_skips_over_existing_fulfilled_ones(self):
        """Se l'assistant dichiara due tool_call e solo una è soddisfatta, il
        backfill va inserito dopo il tool result esistente, non prima."""
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "f1"}},
                    {"id": "c2", "function": {"name": "f2"}},
                ],
            },
            _tool("c1", content="result1"),
            {"role": "user", "content": "done"},
        ]
        result = backfill_missing_tool_results(messages)
        assert result[1] == _tool("c1", content="result1")
        assert result[2] == {
            "role": "tool",
            "tool_call_id": "c2",
            "name": "f2",
            "content": BACKFILL_CONTENT,
        }
        assert result[3] == {"role": "user", "content": "done"}
        assert len(result) == 4

    def test_multiple_assistants_missing_offsets_accumulate_correctly(self):
        messages = [
            _assistant("a1", name="fa"),
            {"role": "user", "content": "mid"},
            _assistant("b1", name="fb"),
            {"role": "user", "content": "end"},
        ]
        result = backfill_missing_tool_results(messages)
        assert result == [
            _assistant("a1", name="fa"),
            {"role": "tool", "tool_call_id": "a1", "name": "fa", "content": BACKFILL_CONTENT},
            {"role": "user", "content": "mid"},
            _assistant("b1", name="fb"),
            {"role": "tool", "tool_call_id": "b1", "name": "fb", "content": BACKFILL_CONTENT},
            {"role": "user", "content": "end"},
        ]

    def test_tool_call_without_id_is_ignored_no_backfill_no_crash(self):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "no-id-tool"}}],
            },
            {"role": "user", "content": "next"},
        ]
        result = backfill_missing_tool_results(messages)
        assert result is messages

    def test_missing_tool_call_without_function_name_backfills_empty_name(self):
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "c9"}]},
        ]
        result = backfill_missing_tool_results(messages)
        assert result[1] == {
            "role": "tool",
            "tool_call_id": "c9",
            "name": "",
            "content": BACKFILL_CONTENT,
        }


class TestMicrocompact:
    """Sostituzione dei risultati tool datati con riassunti a una riga, a blocchi."""

    # Il primo conteggio a cui scatta un blocco.
    FIRST_BATCH = MICROCOMPACT_KEEP_RECENT + MICROCOMPACT_BATCH

    def _long_content(self) -> str:
        return "x" * MICROCOMPACT_MIN_CHARS

    def _short_content(self) -> str:
        return "y" * (MICROCOMPACT_MIN_CHARS - 1)

    def _placeholder(self, tool_name: str) -> str:
        return f"[{tool_name} result omitted from context; call the tool again if you need it]"

    def test_below_first_batch_is_noop(self):
        tool_name = next(iter(COMPACTABLE_TOOLS))
        messages = [
            _tool(str(i), content=self._long_content(), name=tool_name)
            for i in range(self.FIRST_BATCH - 1)
        ]
        assert microcompact(messages) is messages

    def test_first_batch_is_summarized_at_once(self):
        tool_name = next(iter(COMPACTABLE_TOOLS))
        messages = [
            _tool(str(i), content=self._long_content(), name=tool_name)
            for i in range(self.FIRST_BATCH)
        ]
        result = microcompact(messages)
        assert result is not messages
        for i in range(MICROCOMPACT_BATCH):
            assert result[i]["content"] == self._placeholder(tool_name)
        # I "recenti" (ultimi keep_recent) restano intatti.
        for i in range(MICROCOMPACT_BATCH, self.FIRST_BATCH):
            assert result[i]["content"] == self._long_content()

    def test_compacted_set_changes_only_at_batch_boundaries(self):
        """La proprieta' che tiene la cache: aggiungere un risultato non cambia i
        messaggi gia' inviati, se non quando si chiude un blocco."""
        tool_name = next(iter(COMPACTABLE_TOOLS))
        messages = [
            _tool(str(i), content=self._long_content(), name=tool_name)
            for i in range(4 * MICROCOMPACT_BATCH)
        ]
        boundaries = set()
        for k in range(1, len(messages)):
            before = microcompact(messages[:k])
            after = microcompact(messages[: k + 1])
            if after[:k] != before:
                boundaries.add(k + 1)
        assert boundaries == {
            n for n in range(2, len(messages) + 1)
            if n >= self.FIRST_BATCH and (n - MICROCOMPACT_KEEP_RECENT) % MICROCOMPACT_BATCH == 0
        }

    def test_persisted_result_keeps_its_path(self, tmp_path):
        tool_name = next(iter(COMPACTABLE_TOOLS))
        # Il riferimento vero, non una sua copia: se il formato cambia, il
        # segnaposto deve continuare a trovarci il path.
        reference = maybe_persist_tool_result(
            tmp_path, "s", "c0", "z" * 5000, max_chars=MICROCOMPACT_MIN_CHARS,
        )
        saved = tmp_path / ".jenny" / "tool-results" / "s" / "c0.txt"
        assert saved.exists()
        messages = [_tool("0", content=reference, name=tool_name)] + [
            _tool(str(i), content=self._long_content(), name=tool_name)
            for i in range(1, self.FIRST_BATCH)
        ]
        result = microcompact(messages)
        assert result[0]["content"] == (
            f"[{tool_name} result omitted from context; full output still at {saved}]"
        )

    def test_stale_short_content_is_not_compacted(self):
        tool_name = next(iter(COMPACTABLE_TOOLS))
        messages = [
            _tool(str(i), content=self._short_content(), name=tool_name)
            for i in range(self.FIRST_BATCH)
        ]
        result = microcompact(messages)
        # Nessuna voce supera la soglia minima: nessuna modifica effettiva.
        assert result is messages

    def test_stale_non_string_content_is_left_untouched(self):
        tool_name = next(iter(COMPACTABLE_TOOLS))
        messages = [
            {"role": "tool", "tool_call_id": "0", "name": tool_name, "content": ["not", "a", "str"]}
        ] + [
            _tool(str(i), content=self._long_content(), name=tool_name)
            for i in range(1, self.FIRST_BATCH)
        ]
        result = microcompact(messages)
        assert result[0]["content"] == ["not", "a", "str"]

    def test_non_compactable_tool_name_never_counted_or_touched(self):
        messages = [
            _tool(str(i), content=self._long_content(), name="not_in_allowlist")
            for i in range(self.FIRST_BATCH + 5)
        ]
        result = microcompact(messages)
        assert result is messages

    def test_mixed_compactable_and_non_tool_messages(self):
        tool_name = next(iter(COMPACTABLE_TOOLS))
        messages = (
            [{"role": "user", "content": "start"}]
            + [
                _tool(str(i), content=self._long_content(), name=tool_name)
                for i in range(self.FIRST_BATCH + 2)
            ]
            + [{"role": "assistant", "content": "end"}]
        )
        result = microcompact(messages)
        assert result[0] == {"role": "user", "content": "start"}
        assert result[-1] == {"role": "assistant", "content": "end"}
        # Il primo blocco e' compattato; i due in piu' non ne aprono un altro.
        for i in range(1, MICROCOMPACT_BATCH + 1):
            assert result[i]["content"] == self._placeholder(tool_name)
        for i in range(MICROCOMPACT_BATCH + 1, self.FIRST_BATCH + 3):
            assert result[i]["content"] == self._long_content()
