# StudyBench

**A local learning tool for anyone who has to learn a lot of material: students, apprentices, professionals preparing for an exam or a certification.**

Drop in your slides, PDFs, notes and lecture recordings. StudyBench turns them into summaries, practice questions, exam simulations and a chat that answers from your own sources, and schedules what you should repeat and when. It runs on your own computer. The AI is swappable: a local model through Ollama by default, or Claude / OpenAI with your own API key.

Works on **Windows**, **macOS** and Linux. Free, open source (MIT).

## What it does: seven steps

| # | Step | What happens |
|---|------|--------------|
| 1 | **Inventory** | Upload files or import a folder. Only the file types you allow are accepted. Tag, prioritise, and tick "Use" for the files that count. |
| 2 | **Transcribe** | Lecture recordings become text with timestamps (Whisper, on your computer). Link a transcript to its slides, correct the text. |
| 3 | **Interpret** | Summaries in three depths. Long documents are summarised section by section (overview plus every section), cached so a cancelled run resumes. |
| 4 | **Practice** | Spaced repetition, interleaving, question bank (multiple choice, cases, open, audit, oral exam, why/how/what-if), timed exam simulation, AI examiner with follow-ups, free recall, teach-it, mnemonics, weak-point list. |
| 5 | **Chat** | Ask questions; answers come only from your files and cite the passage. |
| 6 | **Recommend** | Real literature from PubMed, OpenAlex and Open Library in German, English or French. Titles come from the databases, never from the AI's memory. |
| 7 | **Present** | Choose content and audience, get editable slides with speaker notes, export to PowerPoint. |

Plus a focus timer (25 min work, 3 min recall, 5 min break) with recovery tips, a progress page, and appearance settings (system / light / dark). The interface is in English; the content can be in German, English or French.

## Install and start

You need an internet connection for the first start only, and some patience: the AI model and the speech model are several gigabytes. They download by themselves in the background (progress is shown bottom left in the app).

### Windows
1. Download this repository (green **Code** button, **Download ZIP**) and unzip it.
2. Double-click **`run.bat`**.
3. It installs what is missing (Python packages, Ollama for the local AI, Tesseract for scanned PDFs) and opens your browser at <http://127.0.0.1:8731>.

If `winget` is not available, install Python 3.9 or newer from [python.org](https://www.python.org/downloads/) (tick "Add to PATH") and Ollama from [ollama.com](https://ollama.com/download) once, then double-click `run.bat` again.

### macOS
1. Download this repository (green **Code** button, **Download ZIP**) and unzip it.
2. Double-click **`run.command`**.
   - The first time, macOS may say the file "cannot be opened because it is from an unidentified developer". Right-click the file, choose **Open**, then **Open** again. Or, in Terminal: `xattr -dr com.apple.quarantine /path/to/studybench`.
   - If double-clicking does nothing because the file lost its "executable" flag during download, open Terminal, type `bash ` (with a space), drag `run.command` into the window and press Enter.
3. The launcher looks for Python 3.9+, creates its own environment, installs the packages and, if [Homebrew](https://brew.sh) is present, installs Ollama and Tesseract. Without Homebrew it tells you what to install by hand: Python from [python.org](https://www.python.org/downloads/macos/), Ollama from [ollama.com](https://ollama.com/download). The app opens at <http://127.0.0.1:8731>.

Apple Silicon and Intel Macs are both supported. Transcription runs on the processor (no GPU speed-up), so long recordings take a while; choose the `small` or `base` speech model in Settings if it is too slow.

### Linux
Run `./run.sh` (needs `python3`, `python3-venv`; optional: Ollama, Tesseract).

### Manual start (any system)
```
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

### The address
StudyBench uses its own port, **8731**, so it does not clash with other local tools. If 8731 is busy it takes the next free one and prints the address. Set `STUDYBENCH_PORT` to choose another. If StudyBench is already running, starting it again just opens the existing one.

## Use the same data on several computers (Windows and Mac)
Everything you create (topics, files, transcripts, summaries, questions, progress) lives in one **data folder**. By default that is the `data` folder next to the program, on this computer only. To work on a Windows PC and a Mac with the same data, put the data folder in a cloud drive:

1. Open **Settings → Data location**. StudyBench lists the OneDrive, Google Drive, iCloud Drive and Dropbox folders it finds.
2. Click **Move my data here** next to your drive. This copies your data to a `StudyBench-Data` folder in the drive and uses it from now on (the old copy stays where it was).
3. On the second computer, install StudyBench and start it. If your cloud drive already holds a `StudyBench-Data` folder, StudyBench connects to it automatically on a fresh install. Otherwise open Settings → Data location and click **Use this data**.

How it works: each computer remembers where the data folder is (a small file in `~/.studybench`), and the data folder itself is identical on every system. Files are stored with paths relative to the folder, so they open on Windows and Mac alike.

Things to know:
- **One computer at a time.** Close StudyBench before you switch computers and let the drive finish syncing. If another computer is using the folder, a warning is shown. Two computers editing at the same time can overwrite each other.
- **API keys stay local.** Keys for Claude or OpenAI are stored in `~/.studybench` on each computer, never in the shared folder. Enter them once per computer. Other settings are shared.
- **Backups.** A daily copy of the database (last 7 days) is kept in `backups/` inside the data folder.
- **Folders imported without copying** (the "copy files" box unticked) are only linked, not stored in the data folder, so they are not available on the other computer. Keep "copy" ticked for anything you want to share.
- Speech and OCR language data are downloaded per computer, not shared.
- Use a drive folder that is kept offline ("Always keep on this device" in OneDrive, "Available offline" in Google Drive) so the database is not fetched on demand.
- Any folder works, not only cloud drives: a USB drive or a network share, for example.

## Settings
Choose the AI provider and model (the **Demo** provider lets you try every screen without AI), appearance, accepted file types, speech model, processor (auto / CPU / GPU) and a glossary of specialist words for transcription. An optional "fast model" (for example `llama3.2:3b`) speeds up long documents.

## Privacy
- With the default local AI (Ollama) and local transcription, nothing leaves your computer. Your files, database and settings live in the data folder (`data/` by default, or the folder you chose under Settings → Data location); back them up by copying it.
- If you enter a Claude or OpenAI key, the **text of your documents and questions is sent to that provider**. Do not use confidential or personal data with a cloud AI.
- `data/` is excluded from Git (`.gitignore`). Never commit it: it contains your files and your API key.

## Good to know
- AI output can be wrong. Summaries and answers are study aids: check facts, numbers and rules against the original source before relying on them.
- Quality depends on the model. A small local model is weaker than a large cloud model, but private and free.
- Scanned PDFs need Tesseract (installed by the launchers where possible).
- Transcription with an NVIDIA GPU needs the CUDA libraries; if they are missing StudyBench falls back to the CPU automatically.

## Troubleshooting
| Problem | What to do |
|---|---|
| "Ollama is not installed" | Install it from [ollama.com](https://ollama.com/download), restart StudyBench. Or pick Claude / OpenAI in Settings. |
| `HTTP 404 ... model not found` | Wait: the model is still downloading (progress bottom left). |
| Transcription is very slow | Use the `small` or `base` model in Settings, or a GPU on Windows/Linux. |
| Browser does not open | Open the address printed in the terminal window. |
| macOS blocks `run.command` | See the macOS steps above (right-click, Open). |

## Tests
```
python tests/test_api.py
python tests/test_api2.py
python tests/test_big.py
```
They run offline with a built-in demo AI and a fake transcription engine. A GitHub Actions workflow runs them on Windows, macOS and Linux for every push.

## Contributing
Issues and pull requests are welcome, especially reports from macOS and Linux. Please include your system, the launcher you used and the text printed in the terminal.

## License
MIT, see `LICENSE`.
