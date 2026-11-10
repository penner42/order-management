namespace OrderManager.Core;

public sealed record DeploymentSettings(string ProjectName, string ComposeFile, string SourceMarker)
{
    public static DeploymentSettings Windows { get; } = new("order-manager-windows", "docker-compose.windows.yml", "windows/Dockerfile");
    public static DeploymentSettings Mac { get; } = new("order-manager-macos", "docker-compose.macos.yml", "macos/Helper/OrderManager.Mac.csproj");

    public void ValidateSource(string directory)
    {
        if (!File.Exists(Path.Combine(directory, ComposeFile)) || !File.Exists(Path.Combine(directory, SourceMarker)))
            throw new InvalidDataException($"This commit does not contain deployment files for {ProjectName}.");
    }
}
