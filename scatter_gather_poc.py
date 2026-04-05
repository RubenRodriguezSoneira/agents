from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAIError


GITHUB_MODELS_ENDPOINT = "https://models.inference.ai.azure.com"
DEFAULT_MODEL = "gpt-4.1"


@dataclass(frozen=True)
class ExpertAgent:
    name: str
    instructions: str


EXPERTS: tuple[ExpertAgent, ...] = (
    ExpertAgent(
        name="AsyncExpert",
        instructions=(
            "You are a C# code reviewer specializing exclusively in async/await issues. "
            "Identify only async/await deadlocks such as calls to .Result or .Wait() that "
            "block the thread, and awaited calls that are missing ConfigureAwait(false). "
            "Report each problem clearly with the line reference."
        ),
    ),
    ExpertAgent(
        name="MemoryExpert",
        instructions=(
            "You are a C# code reviewer specializing exclusively in memory management. "
            "Identify only undisposed IDisposable objects and memory leaks. "
            "Report each problem clearly with the line reference."
        ),
    ),
    ExpertAgent(
        name="ParallelExpert",
        instructions=(
            "You are a C# code reviewer specializing exclusively in concurrency and thread safety. "
            "Identify only thread-safety issues and race conditions, such as non-thread-safe "
            "collections (e.g., List<T>) being modified inside Parallel.ForEach. "
            "Report each problem clearly with the line reference."
        ),
    ),
)


REFACTOR_INSTRUCTIONS = (
    "You are a Lead Software Engineer. You will receive the original C# code "
    "followed by combined feedback from specialist reviewers. "
    "Produce only the final, fully corrected C# code that fixes every identified issue."
)


MESSY_CODE = """
public static List<int> ProcessData(string url)
{
    // Async deadlock: .Result blocks the calling thread
    var httpClient = new HttpClient();
    var response = httpClient.GetAsync(url).Result;
    var content = response.Content.ReadAsStringAsync().Result;

    // Memory leak: MemoryStream is created but never disposed
    var stream = new MemoryStream(System.Text.Encoding.UTF8.GetBytes(content));
    int length = (int)stream.Length;

    // Race condition: List<int> is not thread-safe for concurrent writes
    var results = new List<int>();
    Parallel.ForEach(Enumerable.Range(0, length), i =>
    {
        results.Add(i * 2);
    });

    return results;
}
""".strip()


def _require_token() -> str:
    load_dotenv()
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN environment variable is not set.")
    return token


async def run_agent(
    client: AsyncOpenAI,
    *,
    model: str,
    name: str,
    instructions: str,
    user_prompt: str,
) -> str:
    try:
        response = await client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_prompt},
            ],
        )
    except OpenAIError as exc:
        raise RuntimeError(f"{name} failed to generate a response.") from exc

    content = response.choices[0].message.content
    return content.strip() if content else ""


async def gather_feedback(client: AsyncOpenAI, model: str, source_code: str) -> dict[str, str]:
    review_prompt = (
        "Review the following C# code and report your findings:\n\n"
        f"```csharp\n{source_code}\n```"
    )

    tasks = [
        run_agent(
            client,
            model=model,
            name=expert.name,
            instructions=expert.instructions,
            user_prompt=review_prompt,
        )
        for expert in EXPERTS
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    feedback_by_agent: dict[str, str] = {}
    for expert, result in zip(EXPERTS, results):
        if isinstance(result, Exception):
            raise RuntimeError(
                "Failed to complete the concurrent expert workflow. "
                "Verify the GITHUB_TOKEN is valid and the model endpoint is reachable."
            ) from result
        feedback_by_agent[expert.name] = result or "No findings returned."

    return feedback_by_agent


def build_combined_feedback(feedback_by_agent: dict[str, str]) -> str:
    sections = []
    for agent_name, feedback in feedback_by_agent.items():
        sections.append(f"### {agent_name}\n{feedback}\n")
    return "\n".join(sections).strip()


async def refactor_code(
    client: AsyncOpenAI,
    *,
    model: str,
    source_code: str,
    combined_feedback: str,
) -> str:
    refactor_prompt = f"""
Original code:

```csharp
{source_code}
```

Expert feedback:

{combined_feedback}

Please produce the final, refactored C# code that fixes all issues identified above.
""".strip()

    final_code = await run_agent(
        client,
        model=model,
        name="RefactorAgent",
        instructions=REFACTOR_INSTRUCTIONS,
        user_prompt=refactor_prompt,
    )
    return final_code


async def main() -> None:
    github_token = _require_token()
    model = os.getenv("GITHUB_MODEL", DEFAULT_MODEL)

    client = AsyncOpenAI(api_key=github_token, base_url=GITHUB_MODELS_ENDPOINT)

    feedback_by_agent = await gather_feedback(client, model, MESSY_CODE)
    combined_feedback = build_combined_feedback(feedback_by_agent)
    print(combined_feedback)

    refactored_code = await refactor_code(
        client,
        model=model,
        source_code=MESSY_CODE,
        combined_feedback=combined_feedback,
    )

    print("=== Final Refactored Code ===")
    print(refactored_code)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        raise SystemExit(f"Error: {exc}") from exc