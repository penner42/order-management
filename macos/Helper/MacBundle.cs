namespace OrderManager.Mac;

public static class MacBundle
{
    // Managed PE assemblies are resources, not native nested code. Keep the
    // self-contained runtime together so apphost and native library probing work.
    public const string HelperPath = "Contents/Resources/helper/OrderManager.Mac";

    public static string FromHelperDirectory(string directory) =>
        Path.GetFullPath(Path.Combine(directory, "../../.."));

    public static string BootstrapFromHelperDirectory(string directory) =>
        Path.Combine(FromHelperDirectory(directory), "Contents/Resources/bootstrap.json");
}
