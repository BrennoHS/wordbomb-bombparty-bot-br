"""
Bomb Party BR - bot autônomo (OCR na tela).

Fluxo: lê a sílaba na tela -> escolhe a MAIOR palavra do dicionário que a contém
-> digita -> Enter. Só age quando detecta que é a sua vez.

Uso:
  python bombparty_bot.py --calibrar   (1ª vez: marca a região da sílaba e a caixa de digitação)
  python bombparty_bot.py              (roda o bot)

Teclas:  F8 liga/desliga | F9 zera lista de palavras já usadas | ESC sai
"""
import argparse
import ctypes
import json
import random
import re
import sys
import time
import unicodedata
from pathlib import Path

import keyboard
import mss
import pytesseract
from PIL import Image, ImageOps

BASE = Path(__file__).parent
CONFIG = BASE / "config.json"
DEFAULT_DICT = BASE / "bomb-party-br.html"
MISSING_LOG = BASE / "silabas_faltando.json"
EVENT_LOG = BASE / "bot.log"
FAIL_DIR = BASE / "ocr_falhas"

# Caminho padrão do Tesseract no Windows (ajuste se instalou em outro lugar)
TESSERACT_EXE = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if Path(TESSERACT_EXE).exists():
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def norm(s: str) -> str:
    return strip_accents(s.lower())


def load_words(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".html":
        m = re.search(r"const DICTIONARY\s*=\s*(\[.*?\]);", text, re.S)
        if not m:
            sys.exit("Não achei o array DICTIONARY no HTML.")
        words = json.loads(m.group(1))
    else:
        words = [w.strip() for w in text.splitlines() if w.strip()]
    return list(dict.fromkeys(words))


# ---------- calibração ----------
def cursor_pos() -> tuple[int, int]:
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    p = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def capture_point(msg: str, secs: int = 4) -> tuple[int, int]:
    print(msg)
    for i in range(secs, 0, -1):
        print(f"  capturando em {i}...", end="\r")
        time.sleep(1)
    pos = cursor_pos()
    print(f"  capturado: {pos}      ")
    return pos


def pixel(x: int, y: int) -> list[int]:
    with mss.mss() as sct:
        img = sct.grab({"left": x, "top": y, "width": 1, "height": 1})
        return list(img.pixel(0, 0))


def calibrate() -> None:
    print("=== CALIBRAÇÃO === (entre numa sala e espere chegar a SUA VEZ)\n")
    x1, y1 = capture_point("1) Mouse no canto SUPERIOR ESQUERDO da sílaba.")
    x2, y2 = capture_point("2) Mouse no canto INFERIOR DIREITO da sílaba.")
    tx, ty = capture_point(
        "3) Na SUA VEZ: mouse dentro da caixa de digitação (em área que só\n"
        "   muda de cor quando é sua vez, ex.: no fundo da caixa).",
        6,
    )
    cfg = {
        "region": {
            "left": min(x1, x2),
            "top": min(y1, y2),
            "width": abs(x2 - x1),
            "height": abs(y2 - y1),
        },
        "turn_point": [tx, ty],
        "turn_color": pixel(tx, ty),
    }
    CONFIG.write_text(json.dumps(cfg, indent=2))
    print(f"\nSalvo em {CONFIG}")


# ---------- OCR ----------
def segment(img: Image.Image) -> list[tuple[int, int]]:
    """Colunas (x0, x1) de cada letra (tinta escura em fundo branco)."""
    w, h = img.size
    px = img.load()
    cols = [any(px[x, y] < 128 for y in range(h)) for x in range(w)]
    blobs, start = [], None
    for x, on in enumerate(cols + [False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            blobs.append((start, x - 1))
            start = None
    return blobs


DIGIT_FIX = str.maketrans("0158", "OISB")
WL = "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def ocr_text(img: Image.Image, psm: int, whitelist: bool) -> str:
    """Uma leitura do Tesseract, só letras maiúsculas. A whitelist às vezes engole letras
    redondas (O, S, A), por isso também se lê sem ela."""
    txt = pytesseract.image_to_string(img, config=f"--psm {psm} {WL if whitelist else ''}")
    return re.sub(r"[^A-Z]", "", txt.upper().translate(DIGIT_FIX))


def ocr_char(img: Image.Image, blob: tuple[int, int]) -> str:
    """Lê uma letra isolada."""
    crop = img.crop((max(0, blob[0] - 2), 0, blob[1] + 3, img.height))
    crop = ImageOps.expand(crop, border=20, fill=255)
    for psm, wl in ((10, False), (8, False), (10, True), (13, False)):
        t = ocr_text(crop, psm, wl)
        if t:
            return t[0]
    return ""


def geometry_fix(img: Image.Image, letters: str, blobs: list[tuple[int, int]]) -> str:
    """Com 1 forma por letra: força I nas formas estreitas e separa I de L pelo pé."""
    h = img.height
    px = img.load()
    out = []
    for ch, (x0, x1) in zip(letters, blobs):
        ys = [y for y in range(h) if any(px[x, y] < 128 for x in range(x0, x1 + 1))]
        top, bot = min(ys), max(ys)
        if (x1 - x0 + 1) / (bot - top + 1) < 0.38:  # forma bem estreita = só pode ser "I"
            ch = "I"
        elif ch in "IL":
            band = max(2, (bot - top + 1) // 6)

            def width(y0: int, y1: int) -> int:
                best = 0
                for y in range(y0, y1):
                    xs = [x for x in range(x0, x1 + 1) if px[x, y] < 128]
                    if xs:
                        best = max(best, xs[-1] - xs[0] + 1)
                return best

            # L tem "pé" (base larga, topo fino); I é simétrico (com ou sem serifa)
            foot = width(bot - band + 1, bot + 1) / max(1, width(top, top + band))
            ch = "L" if foot > 1.5 else "I"
        out.append(ch)
    return "".join(out)


def merged_blobs(img: Image.Image, blobs: list[tuple[int, int]]) -> bool:
    """True se alguma forma é larga demais para uma letra só (duas letras coladas)."""
    px = img.load()
    for x0, x1 in blobs:
        ys = [y for y in range(img.height) if any(px[x, y] < 128 for x in range(x0, x1 + 1))]
        if ys and (x1 - x0 + 1) / (max(ys) - min(ys) + 1) > 1.4:
            return True
    return False


def resolve_letters(img: Image.Image) -> str:
    """Junta várias leituras e escolhe a que bate com o nº de letras (formas) da imagem."""
    blobs = segment(img)
    n = len(blobs)
    reads: list[str] = []
    for psm, wl in ((8, False), (7, False), (8, True), (7, True), (13, False)):
        t = ocr_text(img, psm, wl)
        if t:
            reads.append(t)
        # duas leituras iguais (>= 2 letras) e coerentes com as formas: já basta
        if any(len(r) >= 2 and reads.count(r) >= 2 and len(r) == n for r in reads):
            break
    # leituras concordando entre si valem mais que a contagem de formas quando há
    # letras coladas (uma forma bem mais larga que alta)
    agreed = [r for r in reads if len(r) >= 2 and reads.count(r) >= 2]
    if agreed and (len(agreed[0]) == n or merged_blobs(img, blobs)):
        best = max(set(agreed), key=agreed.count)
        return geometry_fix(img, best, blobs) if len(best) == n else best
    matching = [r for r in reads if len(r) == n]
    if matching:
        best = max(set(matching), key=matching.count)
        return geometry_fix(img, best, blobs)
    if 2 <= n <= 5:  # leituras não batem com as formas: lê letra por letra
        per_char = "".join(ocr_char(img, b) or "?" for b in blobs)
        if "?" not in per_char:
            return geometry_fix(img, per_char, blobs)
    # letras coladas / ruído: fica com a leitura mais comum (>= 2 letras)
    reads = [r for r in reads if len(r) >= 2]
    return max(set(reads), key=reads.count) if reads else ""


def recognize(img: Image.Image, thresh: int = 140) -> tuple[str, Image.Image]:
    """img em tons de cinza (já recortada) -> (sílaba normalizada, imagem processada)."""
    img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    # texto claro em fundo escuro -> inverte para texto escuro em fundo claro
    if sum(img.getdata()) / (img.width * img.height) < 128:
        img = ImageOps.invert(img)
    img = ImageOps.autocontrast(img)
    img = img.point(lambda v: 255 if v > thresh else 0)
    img = ImageOps.expand(img, border=20, fill=255)
    return norm(resolve_letters(img)), img


def read_syllable(region: dict, thresh: int = 140) -> tuple[str, Image.Image]:
    with mss.mss() as sct:
        shot = sct.grab(region)
    return recognize(Image.frombytes("RGB", shot.size, shot.rgb).convert("L"), thresh)


def vote_syllable(region: dict, first: str) -> str:
    """Re-lê com limiares diferentes e devolve a leitura mais frequente (tira erros de OCR)."""
    reads = [first] + [read_syllable(region, th)[0] for th in (100, 120, 170, 190)]
    reads = [r for r in reads if len(r) >= 2]
    return max(set(reads), key=reads.count) if reads else first


def is_my_turn(cfg: dict, tol: int = 40) -> bool:
    x, y = cfg["turn_point"]
    cur = pixel(x, y)
    return all(abs(a - b) <= tol for a, b in zip(cur, cfg["turn_color"]))


# ---------- escolha da palavra ----------
def best_word(words, syl, used, max_len):
    cands = [
        w for w in words
        if w not in used and len(w) <= max_len and syl in norm(w)
    ]
    return max(cands, key=len) if cands else None


def type_word(word: str, wpm_min: float, wpm_max: float, long_len: int = 25) -> None:
    """Digita como gente: velocidade que deriva, rajadas, micro-pausas e hesitações.

    1 palavra = 5 caracteres. Palavras longas (>= long_len) usam a metade de cima da
    faixa, mas com a mesma irregularidade (sem ficar cravado no máximo).
    """
    if len(word) >= long_len:
        wpm_min = wpm_min + (wpm_max - wpm_min) * 0.55
    span = max(wpm_max - wpm_min, 1.0)
    wpm = random.uniform(wpm_min, wpm_max)
    burst = 0  # letras restantes de uma "rajada" (trecho digitado mais rápido)
    for i, ch in enumerate(word):
        wpm = min(wpm_max, max(wpm_min, wpm + random.gauss(0, span * 0.25)))
        cur = wpm * random.uniform(0.82, 1.18)
        if burst > 0:
            cur *= 1.25
            burst -= 1
        elif random.random() < 0.12:
            burst = random.randint(3, 6)
        keyboard.write(ch)
        delay = 60 / (cur * 5)
        if ch in "-" or random.random() < 0.05:  # hifens e "tropeços" ganham micro-pausa
            delay += random.uniform(0.06, 0.18)
        if i and i % random.randint(6, 10) == 0 and random.random() < 0.35:
            delay += random.uniform(0.12, 0.30)  # hesitação no meio da palavra
        time.sleep(delay)


def event(kind: str, syl: str = "", extra: str = "") -> None:
    """Uma linha por acontecimento em bot.log (para diagnóstico)."""
    with EVENT_LOG.open("a", encoding="utf-8") as f:
        f.write("\t".join([time.strftime("%H:%M:%S"), kind, syl, extra]) + "\n")


def save_fail(img: Image.Image) -> None:
    FAIL_DIR.mkdir(exist_ok=True)
    if len(list(FAIL_DIR.iterdir())) < 60:
        img.save(FAIL_DIR / f"{time.strftime('%H%M%S')}.png")


def log_missing(syl: str) -> None:
    """Conta sílabas que não existem em nenhuma palavra do dicionário."""
    data = json.loads(MISSING_LOG.read_text(encoding="utf-8")) if MISSING_LOG.exists() else {}
    data[syl] = data.get(syl, 0) + 1
    ordered = dict(sorted(data.items(), key=lambda kv: -kv[1]))
    MISSING_LOG.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrar", action="store_true")
    ap.add_argument("--dict", type=Path, default=DEFAULT_DICT)
    ap.add_argument("--max-len", type=int, default=999, help="limite de letras (sem limite por padrão)")
    ap.add_argument("--wpm-min", type=float, default=48, help="velocidade mínima (palavras/min)")
    ap.add_argument("--wpm-max", type=float, default=99, help="velocidade máxima (palavras/min)")
    ap.add_argument("--long-len", type=int, default=25, help="palavras com esse nº de letras ou mais usam o topo da faixa de WPM")
    ap.add_argument("--reacao-min", type=float, default=0.6, help="pausa mínima (s) entre ver a sílaba e começar a digitar")
    ap.add_argument("--reacao-max", type=float, default=1.5, help="pausa máxima (s) idem")
    ap.add_argument("--debug", action="store_true", help="mostra o que o OCR lê")
    args = ap.parse_args()

    if args.calibrar:
        calibrate()
        return
    if not CONFIG.exists():
        sys.exit("Rode primeiro:  python bombparty_bot.py --calibrar")

    cfg = json.loads(CONFIG.read_text())
    words = load_words(args.dict)
    used: set[str] = set()
    active = True
    fail_reported = False
    fail_count = 0
    retrying = False
    corrections: dict[str, str] = {}  # leitura errada -> certa (vale só nesta vez)
    print(f"{len(words)} palavras carregadas. F8 liga/desliga | F9 zera usadas | ESC sai")

    def toggle():
        nonlocal active
        active = not active
        print("BOT", "LIGADO" if active else "PAUSADO")

    keyboard.add_hotkey("f8", toggle)
    keyboard.add_hotkey("f9", lambda: (used.clear(), print("Lista de usadas zerada.")))

    while not keyboard.is_pressed("esc"):
        time.sleep(0.15)
        if not active or not is_my_turn(cfg):
            fail_reported = False
            fail_count = 0
            corrections.clear()
            retrying = False
            continue
        syl, img = read_syllable(cfg["region"])
        if args.debug:
            print("OCR:", repr(syl))
        syl = corrections.get(syl, syl)
        if len(syl) < 2:
            fail_count += 1
            if fail_count >= 3 and not fail_reported:  # ignora transições rápidas
                fail_reported = True
                save_fail(img)
                event("ocr_falha", syl)
                print(f"[?] vez detectada mas OCR não leu a sílaba (imagem em {FAIL_DIR.name}/)")
            continue
        fail_count = 0
        fail_reported = False
        word = best_word(words, syl, used, args.max_len)
        if not word and not any(syl in norm(w) for w in words):
            # sílaba inexistente: pode ser erro do OCR -> confirma com votação
            voted = vote_syllable(cfg["region"], syl)
            if voted != syl:
                event("ocr_corrigida", syl, voted)
                print(f"[{syl.upper()}] OCR corrigido -> [{voted.upper()}]")
                syl = voted
                word = best_word(words, syl, used, args.max_len)
        if not word:
            if not any(syl in norm(w) for w in words):
                log_missing(syl)
                event("faltando", syl)
                print(f"[{syl.upper()}] NÃO EXISTE no dicionário (registrada em {MISSING_LOG.name})")
            else:
                event("esgotada", syl)
                print(f"[{syl.upper()}] palavras dessa sílaba já usadas/recusadas")
            time.sleep(1)
            continue
        # tempo de "ler a sílaba e pensar": mais curto ao repetir depois de uma recusa,
        # e de vez em quando uma pausa maior (distração)
        if retrying:
            react = random.uniform(0.25, 0.6)
        else:
            react = random.uniform(args.reacao_min, args.reacao_max)
            if random.random() < 0.12:
                react += random.uniform(0.5, 1.2)
        time.sleep(react)
        if not is_my_turn(cfg):  # a vez passou enquanto "pensava"
            continue
        used.add(word)
        print(f"[{syl.upper()}] -> {word}")
        type_word(word, args.wpm_min, args.wpm_max, args.long_len)
        keyboard.press_and_release("enter")
        event("digitou", syl, word)
        time.sleep(0.7)  # dá tempo do jogo passar a vez / rejeitar
        retrying = False
        if is_my_turn(cfg) and read_syllable(cfg["region"])[0] == syl:
            retrying = True
            event("recusada", syl, word)
            print(f"   ↳ jogo recusou '{word}' (anotada em bot.log)")
            # recusa em sequência pode ser sílaba lida errado: confirma por votação
            voted = vote_syllable(cfg["region"], syl)
            if voted != syl:
                corrections[syl] = voted
                event("ocr_corrigida", syl, voted)
                print(f"   ↳ OCR corrigido [{syl.upper()}] -> [{voted.upper()}]")


if __name__ == "__main__":
    main()
