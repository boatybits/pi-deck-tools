"""
InfluxDB Browser/Exporter Tool

- Connects to InfluxDB instance on Pi
- Lists available buckets and metrics
- Allows user to select bucket, metric, and date range
- Downloads selected data as CSV
- tkinter GUI
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from influxdb_client import InfluxDBClient
import pandas as pd
from datetime import datetime

class InfluxDBTool(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("InfluxDB Browser/Exporter")
        self.geometry("700x400")
        self._build_ui()
        self.client = None
        self.buckets = []
        self.metrics = []

    def _build_ui(self):
        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # InfluxDB connection
        ttk.Label(frame, text="InfluxDB URL:").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar(value="http://localhost:8086")
        ttk.Entry(frame, textvariable=self.url_var, width=40).grid(row=0, column=1, sticky="w")
        ttk.Label(frame, text="Token:").grid(row=1, column=0, sticky="w")
        self.token_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.token_var, width=40, show="*").grid(row=1, column=1, sticky="w")
        ttk.Label(frame, text="Org:").grid(row=2, column=0, sticky="w")
        self.org_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.org_var, width=40).grid(row=2, column=1, sticky="w")
        ttk.Button(frame, text="Connect", command=self.connect).grid(row=0, column=2, rowspan=3, padx=10)

        # Buckets
        ttk.Label(frame, text="Bucket:").grid(row=3, column=0, sticky="w")
        self.bucket_var = tk.StringVar()
        self.bucket_combo = ttk.Combobox(frame, textvariable=self.bucket_var, state="readonly", width=37)
        self.bucket_combo.grid(row=3, column=1, sticky="w")
        ttk.Button(frame, text="List Buckets", command=self.list_buckets).grid(row=3, column=2)

        # Metrics
        ttk.Label(frame, text="Metric (measurement):").grid(row=4, column=0, sticky="w")
        self.metric_var = tk.StringVar()
        self.metric_combo = ttk.Combobox(frame, textvariable=self.metric_var, state="readonly", width=37)
        self.metric_combo.grid(row=4, column=1, sticky="w")
        ttk.Button(frame, text="List Metrics", command=self.list_metrics).grid(row=4, column=2)

        # Date range
        ttk.Label(frame, text="Start (YYYY-MM-DD):").grid(row=5, column=0, sticky="w")
        self.start_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.start_var, width=15).grid(row=5, column=1, sticky="w")
        ttk.Label(frame, text="End (YYYY-MM-DD):").grid(row=5, column=2, sticky="w")
        self.end_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.end_var, width=15).grid(row=5, column=3, sticky="w")

        # Download
        ttk.Button(frame, text="Download CSV", command=self.download_csv).grid(row=6, column=0, columnspan=4, pady=10)

    def connect(self):
        try:
            self.client = InfluxDBClient(
                url=self.url_var.get(),
                token=self.token_var.get(),
                org=self.org_var.get(),
                timeout=10_000,
            )
            messagebox.showinfo("Connected", "Connected to InfluxDB.")
        except Exception as e:
            messagebox.showerror("Connection Error", str(e))

    def list_buckets(self):
        if not self.client:
            self.connect()
        try:
            buckets_api = self.client.buckets_api()
            self.buckets = [b.name for b in buckets_api.find_buckets().buckets]
            self.bucket_combo["values"] = self.buckets
            if self.buckets:
                self.bucket_var.set(self.buckets[0])
        except Exception as e:
            messagebox.showerror("Bucket Error", str(e))

    def list_metrics(self):
        if not self.client or not self.bucket_var.get():
            messagebox.showerror("Error", "Connect and select a bucket first.")
            return
        try:
            query = f'''
import "influxdata/influxdb/schema"
schema.measurements(bucket: "{self.bucket_var.get()}")
'''
            tables = self.client.query_api().query(query, org=self.org_var.get())
            self.metrics = [row.get_value() for table in tables for row in table.records]
            self.metric_combo["values"] = self.metrics
            if self.metrics:
                self.metric_var.set(self.metrics[0])
        except Exception as e:
            messagebox.showerror("Metrics Error", str(e))

    def download_csv(self):
        if not self.client or not self.bucket_var.get() or not self.metric_var.get():
            messagebox.showerror("Error", "Connect, select bucket and metric.")
            return
        start = self.start_var.get()
        end = self.end_var.get()
        try:
            start_dt = datetime.strptime(start, "%Y-%m-%d")
            end_dt = datetime.strptime(end, "%Y-%m-%d")
        except Exception:
            messagebox.showerror("Date Error", "Enter valid start/end dates (YYYY-MM-DD)")
            return
        query = f'from(bucket: "{self.bucket_var.get()}")\n  |> range(start: {start}T00:00:00Z, stop: {end}T23:59:59Z)\n  |> filter(fn: (r) => r._measurement == "{self.metric_var.get()}")'
        try:
            tables = self.client.query_api().query_data_frame(query, org=self.org_var.get())
            if isinstance(tables, list):
                df = pd.concat(tables)
            else:
                df = tables
            if df.empty:
                messagebox.showinfo("No Data", "No data found for selection.")
                return
            save_path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
            if save_path:
                df.to_csv(save_path, index=False)
                messagebox.showinfo("Saved", f"CSV saved to {save_path}")
        except Exception as e:
            messagebox.showerror("Download Error", str(e))

if __name__ == "__main__":
    app = InfluxDBTool()
    app.mainloop()
