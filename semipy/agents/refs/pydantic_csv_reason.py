from pydantic_ai import (
    Agent, RunContext,
    AgentRunResultEvent,
    AgentStreamEvent,
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
    ThinkingPartDelta,
    ToolCallPartDelta,
)
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings, OpenRouterProvider
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from collections.abc import AsyncIterable
from datetime import date, datetime
import os
import asyncio
import pandas as pd
from dotenv import load_dotenv
load_dotenv()

model = OpenRouterModel(
    # 'z-ai/glm-5',
    'anthropic/claude-opus-4.6',
    provider=OpenRouterProvider(
        api_key=os.getenv("OPENROUTER_API_KEY")
    )
)
settings = OpenRouterModelSettings(
    openrouter_reasoning={'effort': 'high'},
    temperature=0.0,
)
# openai_model = OpenAIResponsesModel('gpt-5.2')
# openai_settings = OpenAIResponsesModelSettings(
#     openai_reasoning_effort='high',
#     openai_reasoning_summary='detailed',
# )


agent = Agent[None, str](model, model_settings=settings)


output_messages: list[str] = []

async def handle_event(event: AgentStreamEvent):
    if isinstance(event, PartStartEvent):
        output_messages.append(f'[Request] Starting part {event.index}: {event.part!r}')
    elif isinstance(event, PartDeltaEvent):
        if isinstance(event.delta, TextPartDelta):
            output_messages.append(f'[Request] Part {event.index} text delta: {event.delta.content_delta!r}')
        elif isinstance(event.delta, ThinkingPartDelta):
            output_messages.append(f'[Request] Part {event.index} thinking delta: {event.delta.content_delta!r}')
        elif isinstance(event.delta, ToolCallPartDelta):
            output_messages.append(f'[Request] Part {event.index} args delta: {event.delta.args_delta}')
    elif isinstance(event, FunctionToolCallEvent):
        output_messages.append(
            f'[Tools] The LLM calls tool={event.part.tool_name!r} with args={event.part.args} (tool_call_id={event.part.tool_call_id!r})'
        )
    elif isinstance(event, FunctionToolResultEvent):
        output_messages.append(f'[Tools] Tool call {event.tool_call_id!r} returned => {event.result.content}')
    elif isinstance(event, FinalResultEvent):
        output_messages.append(f'[Result] The model starting producing a final result (tool_name={event.tool_name})')


async def event_stream_handler(
    ctx: RunContext,
    event_stream: AsyncIterable[AgentStreamEvent],
):
    async for event in event_stream:
        await handle_event(event)


df = pd.read_csv('/Users/r4yen/Desktop/Research/semi-formal/repo/pips/tests/data/seattle-weather.csv')
test_csv = df.to_string(index=False)

async def main():
    async for event in agent.run_stream_events(f"dont look at the following data.\n\n{test_csv}"):
        if isinstance(event, AgentRunResultEvent):
            output_messages.append(f'[Final Output] {event.result.output}')
        else:
            await handle_event(event)


if __name__ == "__main__":
    asyncio.run(main())
    print(output_messages)

    with open(f'./tests/outputs/pydantic_csv_reason_output_{(model.model_name).split("/")[0]}_{datetime.now().strftime("%Y-%m-%d_%H-%M-%S")}.txt', 'w') as f:
        f.write('\n'.join(output_messages))
