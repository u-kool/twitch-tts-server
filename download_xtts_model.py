# download_xtts_model.py
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from core.xtts_engine import XTTSv2Engine

engine = XTTSv2Engine()
if engine.is_model_downloaded():
    print("Model already downloaded")
    sys.exit(0)

model_dir = engine.model_dir
print(f"Model directory: {model_dir.resolve()}")
print()
print("If direct download fails, manually download from:")
print("  https://huggingface.co/coqui/XTTS-v2/tree/v2.0.2")
print("and place files in the folder above.")
print()

def progress(filename, downloaded, total):
    if total:
        pct = downloaded / total * 100
        print(f"  {filename}: {downloaded//1024**2}MB / {total//1024**2}MB ({pct:.0f}%)")
    else:
        print(f"  {filename}: {downloaded//1024**2}MB")

print("Downloading XTTSv2 model...")
try:
    engine.download_model(progress_callback=progress)
    print("Done")
except Exception as e:
    print(f"Error: {e}")
    print()
    print("Try downloading manually from:")
    print("  https://huggingface.co/coqui/XTTS-v2/tree/v2.0.2")
    print(f"and place files in: {model_dir.resolve()}")
    sys.exit(1)
