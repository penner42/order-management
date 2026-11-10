using System.Diagnostics;

namespace OrderManager.Tray;

internal sealed class DownloadProgressForm : Form
{
    private readonly ProgressBar progress = new() { Dock = DockStyle.Fill, Style = ProgressBarStyle.Marquee };
    private readonly Label details = new() { Dock = DockStyle.Fill, Text = "Connecting…", AutoSize = true };

    public DownloadProgressForm()
    {
        Text = "Download Docker Desktop";
        ClientSize = new Size(440, 120);
        AutoScaleMode = AutoScaleMode.Font;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        ControlBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        var layout = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            Padding = new Padding(16),
            ColumnCount = 1,
            RowCount = 3,
        };
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 35));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 30));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 35));
        layout.Controls.Add(new Label { Text = "Downloading Docker Desktop…", Dock = DockStyle.Fill, AutoSize = true });
        layout.Controls.Add(progress);
        layout.Controls.Add(details);
        Controls.Add(layout);
    }

    public async Task DownloadAsync(HttpClient http, string url, string destination)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(15));
        var cancellation = timeout.Token;
        using var response = await http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, cancellation);
        response.EnsureSuccessStatusCode();
        var total = response.Content.Headers.ContentLength;
        UpdateProgress(0, total);
        await using var source = await response.Content.ReadAsStreamAsync(cancellation);
        await using var file = File.Create(destination);
        var buffer = new byte[81920];
        long received = 0;
        var refresh = Stopwatch.StartNew();
        int count;
        while ((count = await source.ReadAsync(buffer.AsMemory(), cancellation)) != 0)
        {
            await file.WriteAsync(buffer.AsMemory(0, count), cancellation);
            received += count;
            if (refresh.ElapsedMilliseconds >= 100)
            {
                UpdateProgress(received, total);
                refresh.Restart();
            }
        }
        if (total.HasValue && received != total.Value)
            throw new IOException("The Docker Desktop download was incomplete. Retry Start to download it again.");
        UpdateProgress(received, total);
    }

    private void UpdateProgress(long received, long? total)
    {
        var downloaded = received / (1024d * 1024d);
        if (total is > 0)
        {
            var percent = (int)Math.Clamp(received * 100d / total.Value, 0, 100);
            progress.Style = ProgressBarStyle.Continuous;
            progress.Value = percent;
            details.Text = $"{percent}% — {downloaded:F1} MB of {total.Value / (1024d * 1024d):F1} MB";
        }
        else
        {
            progress.Style = ProgressBarStyle.Marquee;
            details.Text = $"{downloaded:F1} MB downloaded";
        }
    }
}
