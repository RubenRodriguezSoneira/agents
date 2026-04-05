using System.Text.Json;
using System.Text.Json.Serialization;
using System.Xml.Linq;
using Microsoft.Build.Locator;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp.Syntax;
using Microsoft.CodeAnalysis.MSBuild;

internal sealed class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private static async Task<int> Main(string[] args)
    {
        var options = ParseArguments(args);
        if (options is null)
        {
            PrintUsage();
            return 2;
        }

        var output = new RepositoryMetadataOutput
        {
            MetadataExtractionStatus = "roslyn",
            GeneratedAt = DateTime.UtcNow.ToString("O"),
            RepositoryRoot = Path.GetFullPath(options.RepoRoot),
        };

        try
        {
            RegisterMsBuild();
            await ExtractMetadataAsync(output, options.RepoRoot);
            output.GeneratedAt = DateTime.UtcNow.ToString("O");
        }
        catch (Exception ex)
        {
            output.MetadataExtractionStatus = "none";
            output.Errors.Add(ex.Message);
            output.GeneratedAt = DateTime.UtcNow.ToString("O");
            await WriteOutputAsync(options.OutputPath, output);
            return 1;
        }

        await WriteOutputAsync(options.OutputPath, output);
        return 0;
    }

    private static async Task WriteOutputAsync(string outputPath, RepositoryMetadataOutput output)
    {
        var targetPath = Path.GetFullPath(outputPath);
        var parentDir = Path.GetDirectoryName(targetPath);
        if (!string.IsNullOrWhiteSpace(parentDir))
        {
            Directory.CreateDirectory(parentDir);
        }

        var payload = JsonSerializer.Serialize(output, JsonOptions);
        await File.WriteAllTextAsync(targetPath, payload);
    }

    private static void RegisterMsBuild()
    {
        if (!MSBuildLocator.IsRegistered)
        {
            MSBuildLocator.RegisterDefaults();
        }
    }

    private static async Task ExtractMetadataAsync(RepositoryMetadataOutput output, string repoRoot)
    {
        using var workspace = MSBuildWorkspace.Create();
        workspace.WorkspaceFailed += (_, eventArgs) =>
        {
            var message = eventArgs.Diagnostic.Message;
            if (!string.IsNullOrWhiteSpace(message))
            {
                output.Errors.Add(message);
            }
        };

        var projects = await LoadProjectsAsync(workspace, repoRoot, output);
        if (projects.Count == 0)
        {
            throw new InvalidOperationException("No C# project files were found for Roslyn metadata extraction.");
        }

        foreach (var project in projects)
        {
            await ProcessProjectAsync(project, repoRoot, output);
        }

        PopulateRelatedFiles(output.Files);
    }

    private static async Task<List<Project>> LoadProjectsAsync(
        MSBuildWorkspace workspace,
        string repoRoot,
        RepositoryMetadataOutput output)
    {
        var projectMap = new Dictionary<string, Project>(StringComparer.OrdinalIgnoreCase);
        var fullRoot = Path.GetFullPath(repoRoot);

        var solutionPaths = Directory.GetFiles(fullRoot, "*.sln", SearchOption.AllDirectories);
        if (solutionPaths.Length > 0)
        {
            foreach (var solutionPath in solutionPaths)
            {
                var solution = await workspace.OpenSolutionAsync(solutionPath);
                foreach (var project in solution.Projects)
                {
                    if (string.IsNullOrWhiteSpace(project.FilePath))
                    {
                        continue;
                    }

                    projectMap[project.FilePath] = project;
                }
            }
        }
        else
        {
            var projectPaths = Directory.GetFiles(fullRoot, "*.csproj", SearchOption.AllDirectories);
            foreach (var projectPath in projectPaths)
            {
                var project = await workspace.OpenProjectAsync(projectPath);
                if (!string.IsNullOrWhiteSpace(project.FilePath))
                {
                    projectMap[project.FilePath] = project;
                }
            }
        }

        return projectMap.Values.ToList();
    }

    private static async Task ProcessProjectAsync(
        Project project,
        string repoRoot,
        RepositoryMetadataOutput output)
    {
        if (string.IsNullOrWhiteSpace(project.FilePath))
        {
            return;
        }

        var compilation = await project.GetCompilationAsync();
        if (compilation is null)
        {
            output.Errors.Add($"Compilation unavailable for project: {project.Name}");
            return;
        }

        var graphNode = new ProjectGraphNode
        {
            Name = project.Name,
            RelativePath = NormalizePath(Path.GetRelativePath(repoRoot, project.FilePath)),
            TargetFramework = ReadTargetFramework(project.FilePath),
            Dependencies = project.ProjectReferences
                .Select(reference => project.Solution.GetProject(reference.ProjectId)?.Name)
                .Where(name => !string.IsNullOrWhiteSpace(name))
                .Cast<string>()
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(name => name, StringComparer.OrdinalIgnoreCase)
                .ToList(),
        };
        output.ProjectGraph.Add(graphNode);

        foreach (var document in project.Documents)
        {
            if (string.IsNullOrWhiteSpace(document.FilePath) ||
                !document.FilePath.EndsWith(".cs", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            var syntaxRoot = await document.GetSyntaxRootAsync();
            if (syntaxRoot is null)
            {
                continue;
            }

            var semanticModel = compilation.GetSemanticModel(syntaxRoot.SyntaxTree, ignoreAccessibility: true);

            var typeDeclaration = syntaxRoot
                .DescendantNodes()
                .OfType<BaseTypeDeclarationSyntax>()
                .FirstOrDefault();

            if (typeDeclaration is null)
            {
                continue;
            }

            var typeSymbol = semanticModel.GetDeclaredSymbol(typeDeclaration) as INamedTypeSymbol;
            if (typeSymbol is null)
            {
                continue;
            }

            var relativePath = NormalizePath(Path.GetRelativePath(repoRoot, document.FilePath));
            if (!output.Files.TryGetValue(relativePath, out var fileMetadata))
            {
                fileMetadata = new FileMetadataNode { RelativePath = relativePath };
                output.Files[relativePath] = fileMetadata;
            }

            fileMetadata.Namespace = typeSymbol.ContainingNamespace?.ToDisplayString() ?? string.Empty;
            fileMetadata.TypeName = typeSymbol.Name;
            fileMetadata.TypeKind = typeSymbol.TypeKind.ToString().ToLowerInvariant();
            fileMetadata.InferredLayer = InferLayer(relativePath, fileMetadata.TypeName);

            if (typeSymbol.BaseType is not null &&
                typeSymbol.BaseType.SpecialType != SpecialType.System_Object &&
                typeSymbol.BaseType.SpecialType != SpecialType.System_ValueType)
            {
                MergeValues(fileMetadata.BaseTypes, new[] { typeSymbol.BaseType.ToDisplayString() });
            }

            MergeValues(fileMetadata.Interfaces, typeSymbol.Interfaces.Select(item => item.ToDisplayString()));

            var constructorDependencies = typeSymbol
                .InstanceConstructors
                .SelectMany(ctor => ctor.Parameters)
                .Select(parameter => parameter.Type.ToDisplayString());
            MergeValues(fileMetadata.ConstructorDependencies, constructorDependencies);

            var attributes = typeSymbol
                .GetAttributes()
                .Select(attribute => attribute.AttributeClass?.Name ?? string.Empty)
                .Select(TrimAttributeSuffix)
                .Where(item => !string.IsNullOrWhiteSpace(item));
            MergeValues(fileMetadata.Attributes, attributes);

            var relatedSymbols = new List<string>();
            relatedSymbols.AddRange(fileMetadata.BaseTypes);
            relatedSymbols.AddRange(fileMetadata.Interfaces);
            relatedSymbols.AddRange(fileMetadata.ConstructorDependencies);

            foreach (var invocation in syntaxRoot.DescendantNodes().OfType<InvocationExpressionSyntax>())
            {
                var invocationSymbol = semanticModel.GetSymbolInfo(invocation).Symbol as IMethodSymbol;
                if (invocationSymbol?.ContainingType is not null)
                {
                    relatedSymbols.Add(invocationSymbol.ContainingType.ToDisplayString());
                }
            }

            MergeValues(fileMetadata.RelatedSymbols, relatedSymbols);
        }
    }

    private static string TrimAttributeSuffix(string attributeName)
    {
        if (attributeName.EndsWith("Attribute", StringComparison.Ordinal))
        {
            return attributeName.Substring(0, attributeName.Length - "Attribute".Length);
        }

        return attributeName;
    }

    private static void PopulateRelatedFiles(Dictionary<string, FileMetadataNode> files)
    {
        var symbolToFile = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        foreach (var (path, metadata) in files)
        {
            if (!string.IsNullOrWhiteSpace(metadata.TypeName))
            {
                symbolToFile[metadata.TypeName] = path;
            }

            if (!string.IsNullOrWhiteSpace(metadata.Namespace) && !string.IsNullOrWhiteSpace(metadata.TypeName))
            {
                symbolToFile[$"{metadata.Namespace}.{metadata.TypeName}"] = path;
            }
        }

        foreach (var (path, metadata) in files)
        {
            var relatedFileSet = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach (var symbol in metadata.RelatedSymbols)
            {
                var clean = symbol.Split('<')[0].Trim();
                if (string.IsNullOrWhiteSpace(clean))
                {
                    continue;
                }

                if (symbolToFile.TryGetValue(clean, out var relatedPath) && !string.Equals(path, relatedPath, StringComparison.OrdinalIgnoreCase))
                {
                    relatedFileSet.Add(relatedPath);
                    continue;
                }

                var shortName = clean.Split('.').Last();
                if (symbolToFile.TryGetValue(shortName, out relatedPath) && !string.Equals(path, relatedPath, StringComparison.OrdinalIgnoreCase))
                {
                    relatedFileSet.Add(relatedPath);
                }
            }

            metadata.RelatedFiles = relatedFileSet
                .OrderBy(item => item, StringComparer.OrdinalIgnoreCase)
                .ToList();
        }
    }

    private static void MergeValues(List<string> target, IEnumerable<string> values)
    {
        var existing = new HashSet<string>(target, StringComparer.OrdinalIgnoreCase);
        foreach (var value in values)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                continue;
            }

            if (existing.Add(value.Trim()))
            {
                target.Add(value.Trim());
            }
        }
    }

    private static string InferLayer(string relativePath, string typeName)
    {
        var probe = $"{relativePath} {typeName}".ToLowerInvariant();

        if (probe.Contains("controller") || probe.Contains("api") || probe.Contains("endpoint") || probe.Contains("presentation"))
        {
            return "presentation";
        }

        if (probe.Contains("handler") || probe.Contains("application") || probe.Contains("usecase") || probe.Contains("service"))
        {
            return "application";
        }

        if (probe.Contains("domain") || probe.Contains("entity") || probe.Contains("aggregate") || probe.Contains("valueobject"))
        {
            return "domain";
        }

        if (probe.Contains("infrastructure") || probe.Contains("repository") || probe.Contains("persistence") || probe.Contains("data"))
        {
            return "infrastructure";
        }

        return "unknown";
    }

    private static string ReadTargetFramework(string csprojPath)
    {
        try
        {
            var document = XDocument.Load(csprojPath);
            var targetFramework = document
                .Descendants()
                .FirstOrDefault(item => item.Name.LocalName == "TargetFramework")
                ?.Value;
            if (!string.IsNullOrWhiteSpace(targetFramework))
            {
                return targetFramework;
            }

            var targetFrameworks = document
                .Descendants()
                .FirstOrDefault(item => item.Name.LocalName == "TargetFrameworks")
                ?.Value;
            if (!string.IsNullOrWhiteSpace(targetFrameworks))
            {
                return targetFrameworks.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                    .FirstOrDefault() ?? "unknown";
            }
        }
        catch
        {
            // Ignore project parsing failures and fallback to unknown.
        }

        return "unknown";
    }

    private static string NormalizePath(string value)
    {
        return value.Replace('\\', '/');
    }

    private static Options? ParseArguments(string[] args)
    {
        string? repoRoot = null;
        string? output = null;

        for (var i = 0; i < args.Length; i++)
        {
            var token = args[i];
            if (string.Equals(token, "--repo-root", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
            {
                repoRoot = args[++i];
                continue;
            }

            if (string.Equals(token, "--output", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
            {
                output = args[++i];
                continue;
            }
        }

        if (string.IsNullOrWhiteSpace(repoRoot) || string.IsNullOrWhiteSpace(output))
        {
            return null;
        }

        return new Options { RepoRoot = repoRoot, OutputPath = output };
    }

    private static void PrintUsage()
    {
        Console.Error.WriteLine("Usage: dotnet run --project RoslynMetadataExtractor -- --repo-root <path> --output <path>");
    }

    private sealed class Options
    {
        public required string RepoRoot { get; init; }
        public required string OutputPath { get; init; }
    }
}

internal sealed class RepositoryMetadataOutput
{
    [JsonPropertyName("metadata_extraction_status")]
    public string MetadataExtractionStatus { get; set; } = "none";

    [JsonPropertyName("generated_at")]
    public string GeneratedAt { get; set; } = string.Empty;

    [JsonPropertyName("repository_root")]
    public string RepositoryRoot { get; set; } = string.Empty;

    [JsonPropertyName("project_graph")]
    public List<ProjectGraphNode> ProjectGraph { get; set; } = new();

    [JsonPropertyName("files")]
    public Dictionary<string, FileMetadataNode> Files { get; set; } =
        new(StringComparer.OrdinalIgnoreCase);

    [JsonPropertyName("errors")]
    public List<string> Errors { get; set; } = new();
}

internal sealed class ProjectGraphNode
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = string.Empty;

    [JsonPropertyName("relative_path")]
    public string RelativePath { get; set; } = string.Empty;

    [JsonPropertyName("target_framework")]
    public string TargetFramework { get; set; } = "unknown";

    [JsonPropertyName("dependencies")]
    public List<string> Dependencies { get; set; } = new();
}

internal sealed class FileMetadataNode
{
    [JsonPropertyName("relative_path")]
    public string RelativePath { get; set; } = string.Empty;

    [JsonPropertyName("namespace")]
    public string Namespace { get; set; } = string.Empty;

    [JsonPropertyName("type_name")]
    public string TypeName { get; set; } = string.Empty;

    [JsonPropertyName("type_kind")]
    public string TypeKind { get; set; } = string.Empty;

    [JsonPropertyName("base_types")]
    public List<string> BaseTypes { get; set; } = new();

    [JsonPropertyName("interfaces")]
    public List<string> Interfaces { get; set; } = new();

    [JsonPropertyName("constructor_dependencies")]
    public List<string> ConstructorDependencies { get; set; } = new();

    [JsonPropertyName("attributes")]
    public List<string> Attributes { get; set; } = new();

    [JsonPropertyName("related_symbols")]
    public List<string> RelatedSymbols { get; set; } = new();

    [JsonPropertyName("related_files")]
    public List<string> RelatedFiles { get; set; } = new();

    [JsonPropertyName("inferred_layer")]
    public string InferredLayer { get; set; } = "unknown";
}
