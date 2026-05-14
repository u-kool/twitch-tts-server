from core.xtts_engine import XTTSv2Engine

e = XTTSv2Engine()
e._load_model()
out = e.generate("Тест голоса", voice="female_01.wav", language="ru")
print("Готово:", out)
