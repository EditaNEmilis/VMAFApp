# Packages
import sys, os, json, time, subprocess
from PyQt5.QtCore import QProcess
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog, QMessageBox,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QCheckBox, QSpinBox, QTextEdit
)

MODELS = ["vmaf_v0.6.1", "vmaf_4k_v0.6.1", "vmaf_v0.6.1neg"] # Model list

def has_libvmaf(ffmpeg_path):
    try:
        r = subprocess.run([ffmpeg_path, "-hide_banner", "-filters"],
                           capture_output=True, text=True)
        return "libvmaf" in (r.stdout or "")
    except Exception:
        return False

def parse_vmaf_json(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    res = {}
    if "pooled_metrics" in data:
        pm = data["pooled_metrics"]
        def g(key, field="mean"):
            x = pm.get(key, {})
            return x.get(field) if isinstance(x, dict) else None
        res["vmaf"] = g("vmaf")
        res["psnr_y"] = g("psnr_y") or g("psnr")
        res["ssim"] = g("ssim")
        res["ms_ssim"] = g("ms_ssim")
        return res
    if "aggregate" in data and "VMAF_score" in data["aggregate"]:
        res["vmaf"] = data["aggregate"]["VMAF_score"]
        return res
    frames = data.get("frames", [])
    vals = [fr.get("metrics", {}).get("vmaf") for fr in frames if "metrics" in fr]
    vals = [v for v in vals if v is not None]
    if vals:
        res["vmaf"] = sum(vals) / len(vals)
    return res

class VMAFWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FFmpeg VMAF")
        self.proc = None
        self.log_path_abs = None
        self._build_ui()

    def _build_ui(self):
        w = QWidget(self)
        self.setCentralWidget(w)
        grid = QGridLayout(w)

        # Paths
        self.ffmpeg_edit = QLineEdit()
        self.dist_edit = QLineEdit()
        self.ref_edit = QLineEdit()

        ffmpeg_btn = QPushButton("Browse")
        ffmpeg_btn.clicked.connect(self.browse_ffmpeg)
        dist_btn = QPushButton("Browse")
        dist_btn.clicked.connect(lambda: self.browse_file(self.dist_edit))
        ref_btn = QPushButton("Browse")
        ref_btn.clicked.connect(lambda: self.browse_file(self.ref_edit))

        row = 0
        grid.addWidget(QLabel("ffmpeg.exe"), row, 0)
        grid.addWidget(self.ffmpeg_edit, row, 1)
        grid.addWidget(ffmpeg_btn, row, 2)

        row += 1
        grid.addWidget(QLabel("Distorted (encoded)"), row, 0)
        grid.addWidget(self.dist_edit, row, 1)
        grid.addWidget(dist_btn, row, 2)

        row += 1
        grid.addWidget(QLabel("Reference (source)"), row, 0)
        grid.addWidget(self.ref_edit, row, 1)
        grid.addWidget(ref_btn, row, 2)

        # Options
        self.model_cb = QComboBox(); self.model_cb.addItems(MODELS)
        self.psnr_cb = QCheckBox("PSNR")
        self.ssim_cb = QCheckBox("SSIM")
        self.msssim_cb = QCheckBox("MS-SSIM")

        self.threads_spin = QSpinBox(); self.threads_spin.setRange(1, 256); self.threads_spin.setValue(os.cpu_count() or 8)
        self.subsample_spin = QSpinBox(); self.subsample_spin.setRange(1, 100); self.subsample_spin.setValue(1)

        # Model selection
        row += 1
        grid.addWidget(QLabel("Model"), row, 0)
        grid.addWidget(self.model_cb, row, 1)
        mbox = QHBoxLayout()
        mbox.addWidget(self.psnr_cb); mbox.addWidget(self.ssim_cb); mbox.addWidget(self.msssim_cb)
        mwrap = QWidget(); mwrap.setLayout(mbox)
        grid.addWidget(mwrap, row, 2)

        # Threads and Subsample
        row += 1
        grid.addWidget(QLabel("Threads"), row, 0)
        grid.addWidget(self.threads_spin, row, 1)
        sbox = QHBoxLayout()
        sbox.addWidget(QLabel("Subsample")); sbox.addWidget(self.subsample_spin)
        swrap = QWidget(); swrap.setLayout(sbox)
        grid.addWidget(swrap, row, 2)

        # Log
        row += 1
        self.keep_log_cb = QCheckBox("Save JSON log")
        self.keep_log_cb.setChecked(True) # DEFAULT: Keep logs
        grid.addWidget(self.keep_log_cb, row, 0, 1, 3)

        # Controls
        self.run_btn = QPushButton("Run VMAF"); self.run_btn.clicked.connect(self.run_vmaf)
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setEnabled(False); self.cancel_btn.clicked.connect(self.cancel_vmaf)
        row += 1
        cbox = QHBoxLayout(); cbox.addWidget(self.run_btn); cbox.addWidget(self.cancel_btn)
        cwrap = QWidget(); cwrap.setLayout(cbox)
        grid.addWidget(cwrap, row, 1, 1, 2)

        # Output
        row += 1
        self.results_label = QLabel("Results: -")
        grid.addWidget(self.results_label, row, 0, 1, 3)

        row += 1
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setMinimumHeight(200)
        grid.addWidget(self.log, row, 0, 1, 3)

        self.resize(820, 520)

    def browse_ffmpeg(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select ffmpeg.exe", "", "ffmpeg (ffmpeg.exe);;All files (*.*)")
        if path: self.ffmpeg_edit.setText(path)

    def browse_file(self, line_edit):
        path, _ = QFileDialog.getOpenFileName(self, "Select video", "", "Videos (*.*)")
        if path: line_edit.setText(path)

    def run_vmaf(self):
        ffmpeg = self.ffmpeg_edit.text().strip().strip('"')
        dist = self.dist_edit.text().strip().strip('"')
        ref  = self.ref_edit.text().strip().strip('"')

        if not ffmpeg or not os.path.isfile(ffmpeg):
            QMessageBox.critical(self, "Error", "Please select a valid ffmpeg.exe"); return
        if not os.path.isfile(dist) or not os.path.isfile(ref):
            QMessageBox.critical(self, "Error", "Please select both distorted and reference videos."); return

        # Working dir = where the distorted file is, and write JSON there
        log_dir = os.path.dirname(dist) or os.getcwd()
        log_name = f"vmaf_{int(time.time())}.json"
        self.log_path_abs = os.path.join(log_dir, log_name)

        self.keep_log = self.keep_log_cb.isChecked()

        self.log.clear()
        self.results_label.setText("Results: running...")
        self.append_log("Checking libvmaf...")
        if not has_libvmaf(ffmpeg):
            QMessageBox.critical(self, "No libvmaf", "This ffmpeg build doesn't include libvmaf."); return

        # Use relative name inside filter (avoids escaping C:KATEX_INLINE_CLOSE
        opts = [
            f"model=version={self.model_cb.currentText()}",
            "log_fmt=json",
            f"log_path={log_name}",
        ]
        if self.psnr_cb.isChecked():   opts.append("psnr=1")
        if self.ssim_cb.isChecked():   opts.append("ssim=1")
        if self.msssim_cb.isChecked(): opts.append("ms_ssim=1")
        t = self.threads_spin.value()
        if t > 0: opts.append(f"n_threads={t}")
        s = self.subsample_spin.value()
        if s > 1: opts.append(f"n_subsample={s}")

        vmaf_filter = "[0:v][1:v]scale2ref=flags=bicubic[dist][ref];[dist][ref]libvmaf=" + ":".join(opts)
        args = ["-hide_banner", "-nostats", "-i", dist, "-i", ref, "-lavfi", vmaf_filter, "-f", "null", "-"]

        self.append_log("Working dir: " + log_dir)
        self.append_log("Will write: " + self.log_path_abs)
        self.append_log("Command:\n" + " ".join([ffmpeg] + [f'"{a}"' if " " in a else a for a in args]))

        # Start one QProcess, keep working directory
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(log_dir)
        self.proc.setProgram(ffmpeg)
        self.proc.setArguments(args)
        self.proc.readyReadStandardError.connect(self.read_stderr)
        self.proc.readyReadStandardOutput.connect(self.read_stdout)
        self.proc.finished.connect(self.proc_finished)
        self.proc.start()

    def cancel_vmaf(self):
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.append_log("\nCancelling...")
            self.proc.kill()

    def read_stderr(self):
        data = bytes(self.proc.readAllStandardError()).decode(errors="ignore")
        if data: self.append_log(data)

    def read_stdout(self):
        data = bytes(self.proc.readAllStandardOutput()).decode(errors="ignore")
        if data: self.append_log(data)

    def proc_finished(self, code, status):
        self.cancel_btn.setEnabled(False)
        self.run_btn.setEnabled(True)

        if code != 0:
            self.append_log(f"\nffmpeg exited with code {code}.")
            QMessageBox.warning(self, "ffmpeg failed", "See log for details.")
            return

        if not os.path.exists(self.log_path_abs):
            self.append_log("\nCompleted, but JSON log not found at:\n" + self.log_path_abs)
            QMessageBox.information(self, "Done", "Finished, but JSON log not found.")
            return

        try:
            res = parse_vmaf_json(self.log_path_abs)
        except Exception as e:
            self.append_log(f"\nFailed to parse JSON: {e}")
            QMessageBox.warning(self, "Parse error", str(e))
            return

        parts = []
        if res.get("vmaf")    is not None: parts.append(f"VMAF (mean): {res['vmaf']:.3f}")
        if res.get("psnr_y")  is not None: parts.append(f"PSNR-Y (mean): {res['psnr_y']:.3f} dB")
        if res.get("ssim")    is not None: parts.append(f"SSIM (mean): {res['ssim']:.5f}")
        if res.get("ms_ssim") is not None: parts.append(f"MS-SSIM (mean): {res['ms_ssim']:.5f}")

        self.results_label.setText("Results: " + (" | ".join(parts) if parts else "No metrics parsed."))
        self.append_log(f"\nLog saved: {self.log_path_abs}")

        if self.keep_log:
            self.append_log(f"\nLog saved: {self.log_path_abs}")
        else:
            try:
                os.remove(self.log_path_abs)
                self.append_log("\nLog discarded (Save JSON log off).")
            except Exception as e:
                self.append_log(f"\nTried to delete log but couldn't: {e}")

    def append_log(self, s):
        self.log.moveCursor(self.log.textCursor().End)
        self.log.insertPlainText(s if s.endswith("\n") else s + "\n")
        self.log.moveCursor(self.log.textCursor().End)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = VMAFWindow()
    win.show()
    sys.exit(app.exec_())