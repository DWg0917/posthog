from django.test import SimpleTestCase

from langchain_core.messages import AIMessage
from parameterized import parameterized

from ee.hogai.chat_agent.memory.parsers import check_memory_collection_completed, compressed_memory_parser


class TestParsers(SimpleTestCase):
    def test_compressed_memory_parser(self):
        memory = "Hello\n\nWorld "
        self.assertEqual(compressed_memory_parser(memory), "Hello\nWorld ")

    @parameterized.expand(
        [
            ("no_tools", "Hello World", [], True),
            ("completed", "[Done]", ["core_memory_append"], True),
            ("valid", "Reasoning", ["core_memory_append", "core_memory_replace"], False),
            ("unknown", "Reasoning", ["execute_sql"], True),
            ("mixed", "Reasoning", ["core_memory_append", "execute_sql"], True),
        ]
    )
    def test_check_memory_collection_completed(
        self, _name: str, content: str, tool_names: list[str], completed: bool
    ) -> None:
        message = AIMessage(
            content=content,
            tool_calls=[{"id": str(i), "args": {}, "name": name} for i, name in enumerate(tool_names)],
        )
        result = check_memory_collection_completed(
            message, allowed_tool_names={"core_memory_append", "core_memory_replace"}
        )
        if completed:
            self.assertIsNone(result)
        else:
            self.assertEqual(result, message)
