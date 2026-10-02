import pytest

from rtagent.domain import AppError, Block, Message, Preset, StudioConfig, Variable
from rtagent.prompts import compile_prompt, condition_value


def compile_blocks(blocks, **kwargs):
    return compile_prompt(
        Preset(blocks=blocks, name="Test", id="test"),
        StudioConfig(),
        [],
        "Task",
        kwargs.get("variables", {}),
        [],
    )


def test_compilation_is_deterministic_and_preserves_slots():
    preset = Preset(
        name="Test",
        blocks=[
            Block(
                id="b",
                name="Context",
                role="user",
                placement="before_input",
                order=1,
                template="Context",
            ),
            Block(id="a", name="Rules", order=2, template="Rules"),
        ],
    )
    args = (preset, StudioConfig(), [Message(role="assistant", content="History")], "Task", {}, [])
    first, second = compile_prompt(*args), compile_prompt(*args)
    assert first == second
    assert [message.content for message in first.messages] == [
        "Rules",
        "History",
        "Context",
        "Task",
    ]
    assert first.source_map[0] == {"message_index": 0, "source": "a"}


def test_filtered_blocks_report_why():
    result = compile_blocks(
        [
            Block(name="Disabled", order=1, template="disabled", enabled=False),
            Block(name="Provider", order=2, template="provider", provider_allowlist=[]),
            Block(name="Model", order=3, template="model", model_allowlist=["other"]),
            Block(
                name="Condition",
                order=4,
                template="condition",
                condition={"field": "provider", "op": "eq", "value": "openai"},
            ),
        ]
    )
    assert len(result.messages) == 1
    assert [row["reason"] for row in result.activation_report] == [
        "disabled",
        "provider_filter",
        "model_filter",
        "condition_false",
    ]


@pytest.mark.parametrize(
    "blocks,code",
    [
        (
            [Block(name="One", order=1, template="a"), Block(name="Two", order=1, template="b")],
            "duplicate_order",
        ),
        ([Block(name="Variable", order=1, template="{{undeclared}}")], "undeclared_variable"),
        (
            [Block(name="Variable", order=1, template="{{x}}", variables={"x": Variable()})],
            "missing_variable",
        ),
        ([Block(name="Unsafe", order=1, template="{{os.system('x')}}")], "invalid_template"),
    ],
)
def test_invalid_compilation(blocks, code):
    with pytest.raises(AppError) as error:
        compile_blocks(blocks)
    assert error.value.code == code


def test_substitution_does_not_reinterpret_text():
    result = compile_blocks(
        [Block(name="Context", order=1, template="{{x}}", variables={"x": Variable()})],
        variables={"x": "{{secret}}"},
    )
    assert result.messages[0].content == "{{secret}}"


def test_context_budget():
    config = StudioConfig()
    config.provider.context_window = 256
    with pytest.raises(AppError, match="context"):
        compile_prompt(Preset(name="Empty"), config, [], "Task", {}, [])


@pytest.mark.parametrize(
    "condition,expected",
    [
        ({"all": []}, True),
        ({"any": []}, False),
        ({"not": {"all": []}}, False),
        ({"field": "variables.x", "op": "exists"}, False),
    ],
)
def test_condition_operators(condition, expected):
    assert condition_value(condition, {"variables": {}}) is expected


def test_missing_condition_field_is_not_silently_false():
    with pytest.raises(AppError):
        condition_value({"field": "variables.x", "op": "eq", "value": 1}, {"variables": {}})
