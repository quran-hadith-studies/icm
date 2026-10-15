# ICM desktop app — installation / نصب برنامه‌ی رومیزی

Runs the final analysis on **your own computer** (about 3 minutes instead of an hour on the server).
تحلیل نهایی را روی رایانه‌ی خودتان اجرا می‌کند (حدود ۳ دقیقه، به‌جای نزدیک یک ساعت روی سرور).

## One-time setup / نصب (یک بار)

### Windows
1. Install **Python 3** from <https://www.python.org/downloads/> (any 3.9–3.12; on the first screen tick **“Add python.exe to PATH”**).
2. Download the code: <https://github.com/quran-hadith-studies/icm/archive/refs/heads/main.zip> and unzip it
   (or `git clone https://github.com/quran-hadith-studies/icm`). Keep the folder; the app runs from it.
3. Double-click **`install-app.bat`** inside the folder. Wait until it says “Done”.

### macOS
1. Install Python 3 from <https://www.python.org/downloads/> (or Homebrew `brew install python`).
2. Download and unzip the code (same link as above), open Terminal in that folder and run `./install-app.sh`.

### Linux
`git clone https://github.com/quran-hadith-studies/icm && cd icm && ./install-app.sh`
(for the native window also install GTK: `sudo apt install python3-gi gir1.2-webkit2-4.1`; otherwise a Chrome/Chromium window is used).

## Every analysis / هر تحلیل
1. On ebadollah.ir/icm, confirm the graph (step 5) and choose any compute mode; on the waiting page press
   **«اجرا با برنامه‌ی رومیزی»**. Your browser asks once whether to open “ICM” — allow it (tick “always”).
2. The app window opens. Press **شروع محاسبه** (Start). Do not close the window until it finishes.
3. Press **فرستادن نتیجه** (Send result). The website page moves on by itself.

If the app does not open: press **«دریافت فایل کار»** on the page, then double-click the downloaded `.icmjob` file.
If sending fails: press **«بارگذاری فایل نتیجه»** on the page and choose the file shown in the app
(`.icmresult` in the `.icm_app/results` folder of your home directory).

## Update / به‌روزرسانی
Download the code again (or `git pull`) into the same folder. The app warns when its model is older than the server's.

## Remove / حذف
`python -m icm app uninstall` in the folder (Windows: use `%USERPROFILE%\.icm_app\venv\Scripts\python.exe`), then delete the folder and `~/.icm_app`.
