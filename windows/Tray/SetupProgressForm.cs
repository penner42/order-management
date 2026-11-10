using System.Diagnostics;
using System.Collections.Concurrent;

namespace OrderManager.Tray;

internal sealed class SetupProgressForm : Form
{
    private readonly ProgressBar progress = new() { Dock = DockStyle.Fill, Style = ProgressBarStyle.Marquee };
    private readonly Label details = new() { Dock = DockStyle.Fill, Text = "Connecting…", AutoSize = true };
    private readonly Label step = new() { Dock = DockStyle.Fill, AutoSize = true };
    private readonly Label elapsed = new() { Dock = DockStyle.Fill, AutoSize = true };
    private readonly TextBox activity = new() { Dock = DockStyle.Fill, Multiline = true, ReadOnly = true,
        ScrollBars = ScrollBars.Vertical, WordWrap = true };
    private readonly Button close = new() { Text = "Close", Enabled = false, AutoSize = true };
    private readonly ConcurrentQueue<string> pending = new();
    private readonly System.Windows.Forms.Timer refresh = new() { Interval = 250 };
    private readonly Stopwatch duration = Stopwatch.StartNew();
    private volatile bool running = true;

    public SetupProgressForm()
    {
        Text = "Order Manager setup progress";
        ClientSize = new Size(680, 440);
        AutoScaleMode = AutoScaleMode.Font;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        var layout = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            Padding = new Padding(16),
            ColumnCount = 1,
            RowCount = 6,
        };
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 32));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 24));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 28));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 28));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 36));
        layout.Controls.Add(step);
        layout.Controls.Add(progress);
        layout.Controls.Add(details);
        layout.Controls.Add(elapsed);
        layout.Controls.Add(activity);
        layout.Controls.Add(close);
        Controls.Add(layout);
        close.Click += (_, _) => Close();
        FormClosing += (_, e) => { if (running && e.CloseReason == CloseReason.UserClosing) e.Cancel = true; };
        refresh.Tick += (_, _) =>
        {
            elapsed.Text = $"Elapsed: {duration.Elapsed:hh\\:mm\\:ss} — " +
                (running ? "Working. Some steps can take several minutes." : "Finished. You can close this window.");
            FlushActivity();
        };
        refresh.Start();
    }

    public void SetStep(string text)
    {
        if (IsDisposed) return;
        step.Text = text;
        details.Text = "Please wait…";
        progress.Style = ProgressBarStyle.Marquee;
        AppendActivity(text);
    }

    // Process output arrives concurrently from worker threads; only the UI timer touches controls.
    public void AppendActivity(string text)
    {
        if (running) pending.Enqueue(text);
    }

    private void FlushActivity()
    {
        var batch = new System.Text.StringBuilder();
        while (pending.TryDequeue(out var line)) batch.AppendLine(line);
        if (batch.Length == 0) return;
        var text = activity.Text + batch;
        activity.Text = text.Length > 32000 ? text[^32000..] : text;
        activity.SelectionStart = activity.TextLength;
        activity.ScrollToCaret();
    }

    public void Complete(string title, string message)
    {
        if (IsDisposed) return;
        running = false;
        duration.Stop();
        step.Text = title;
        details.Text = "";
        progress.Style = ProgressBarStyle.Continuous;
        progress.Value = title == "Complete" ? 100 : 0;
        pending.Enqueue(message);
        FlushActivity();
        close.Enabled = true;
        WindowState = FormWindowState.Normal;
        Activate();
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

    protected override void Dispose(bool disposing)
    {
        if (disposing) { running = false; refresh.Dispose(); }
        base.Dispose(disposing);
    }
}
