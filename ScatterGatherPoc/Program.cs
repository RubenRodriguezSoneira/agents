using System.ClientModel;
using System.Text;
using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.AI;
using OpenAI;

// ---------------------------------------------------------------------------
// 1. Set up IChatClient routed to GitHub Models
// ---------------------------------------------------------------------------
string githubToken = Environment.GetEnvironmentVariable("GITHUB_TOKEN")
    ?? throw new InvalidOperationException("GITHUB_TOKEN environment variable is not set.");

IChatClient chatClient = new OpenAIClient(
        new ApiKeyCredential(githubToken),
        new OpenAIClientOptions { Endpoint = new Uri("https://models.inference.ai.azure.com") })
    .GetChatClient("gpt-5.3-codex")
    .AsIChatClient();

// ---------------------------------------------------------------------------
// 2. Create the four agents using the AsAIAgent() extension
// ---------------------------------------------------------------------------
var asyncExpert = chatClient.AsAIAgent(
    instructions: "You are a C# code reviewer specializing exclusively in async/await issues. " +
                  "Identify only async/await deadlocks such as calls to .Result or .Wait() that " +
                  "block the thread, and awaited calls that are missing ConfigureAwait(false). " +
                  "Report each problem clearly with the line reference.",
    name: "AsyncExpert");

var memoryExpert = chatClient.AsAIAgent(
    instructions: "You are a C# code reviewer specializing exclusively in memory management. " +
                  "Identify only undisposed IDisposable objects and memory leaks. " +
                  "Report each problem clearly with the line reference.",
    name: "MemoryExpert");

var parallelExpert = chatClient.AsAIAgent(
    instructions: "You are a C# code reviewer specializing exclusively in concurrency and thread safety. " +
                  "Identify only thread-safety issues and race conditions, such as non-thread-safe " +
                  "collections (e.g., List<T>) being modified inside Parallel.ForEach. " +
                  "Report each problem clearly with the line reference.",
    name: "ParallelExpert");

var refactorAgent = chatClient.AsAIAgent(
    instructions: "You are a Lead Software Engineer. You will receive the original C# code " +
                  "followed by combined feedback from specialist reviewers. " +
                  "Produce only the final, fully corrected C# code that fixes every identified issue.",
    name: "RefactorAgent");

// ---------------------------------------------------------------------------
// 3. Define the messy code: deadlock + undisposed stream + race condition
// ---------------------------------------------------------------------------
const string messyCode = """
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
    """;

// ---------------------------------------------------------------------------
// 4. Scatter: run the three expert agents concurrently via BuildConcurrent()
// ---------------------------------------------------------------------------
var expertWorkflow = AgentWorkflowBuilder.BuildConcurrent(
    [asyncExpert, memoryExpert, parallelExpert],
    aggregator: null);

var inputMessages = new List<ChatMessage>
{
    new(ChatRole.User, $"Review the following C# code and report your findings:\n\n```csharp\n{messyCode}\n```")
};

var feedbackByAgent = new Dictionary<string, StringBuilder>();

StreamingRun streamingRun;
try
{
    streamingRun = await InProcessExecution.RunStreamingAsync(expertWorkflow, inputMessages);
}
catch (Exception ex)
{
    throw new InvalidOperationException(
        "Failed to start the concurrent expert workflow. " +
        "Verify the GITHUB_TOKEN is valid and the model endpoint is reachable.", ex);
}

await using (streamingRun)
{
    await streamingRun.TrySendMessageAsync(new TurnToken(emitEvents: true));

    // -------------------------------------------------------------------------
    // 5. Gather: iterate events and accumulate each expert's output
    // -------------------------------------------------------------------------
    await foreach (WorkflowEvent evt in streamingRun.WatchStreamAsync())
    {
        if (evt is AgentResponseUpdateEvent updateEvent)
        {
            string agentName = updateEvent.Update.AuthorName ?? updateEvent.ExecutorId ?? "Expert";
            if (!feedbackByAgent.TryGetValue(agentName, out var agentFeedback))
                feedbackByAgent[agentName] = agentFeedback = new StringBuilder();
            agentFeedback.Append(updateEvent.Update.Text);
        }
    }
}

// Build a single combined feedback string from all three experts
var combinedFeedback = new StringBuilder();
foreach (var (agentName, feedback) in feedbackByAgent)
{
    combinedFeedback.AppendLine($"### {agentName}");
    combinedFeedback.AppendLine(feedback.ToString());
    combinedFeedback.AppendLine();
}

// ---------------------------------------------------------------------------
// 6. Refactor: pass original code + combined feedback to the RefactorAgent
// ---------------------------------------------------------------------------
string refactorPrompt =
    $"""
    Original code:

    ```csharp
    {messyCode}
    ```

    Expert feedback:

    {combinedFeedback}

    Please produce the final, refactored C# code that fixes all issues identified above.
    """;

AgentResponse refactorResponse;
try
{
    refactorResponse = await refactorAgent.RunAsync(refactorPrompt);
}
catch (Exception ex)
{
    throw new InvalidOperationException(
        "RefactorAgent failed to produce the final code. " +
        "Check API rate limits and the validity of the combined feedback.", ex);
}

// ---------------------------------------------------------------------------
// 7. Print the final refactored code
// ---------------------------------------------------------------------------
Console.WriteLine("=== Final Refactored Code ===");
Console.WriteLine(refactorResponse.Text);
