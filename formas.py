"""Reconhecimento de letras por comparação de formas (moldes aprendidos).

O jogo desenha a sílaba sempre com a mesma fonte, então cada letra tem praticamente o
mesmo desenho toda vez. Guardamos "moldes" (bitmaps normalizados) de cada letra e
comparamos as letras da tela com eles: exato e muito mais rápido que OCR.

Os moldes são aprendidos sozinhos: quando o OCR lê uma sílaba com segurança, o desenho de
cada letra vira um molde (arquivo moldes.json).
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image

MOLDES_JSON = Path(__file__).parent / "moldes.json"
SIZE = (20, 28)        # largura x altura do bitmap normalizado
MAX_VARIANTS = 6       # moldes guardados por letra
MATCH_MAX = 0.10       # distância máxima aceita para reconhecer uma letra
MARGIN = 0.03          # a 2ª letra mais parecida precisa ser pelo menos isso pior
DEDUPE = 0.02          # molde novo muito parecido com um existente não é guardado
CONFLICT = 0.05        # ao aprender, não aceita letra que já "é" outra letra assim
ASPECT_W = 0.3         # peso da proporção largura/altura na distância


def ink_rows(img: Image.Image, blob: tuple[int, int]) -> tuple[int, int] | None:
    px = img.load()
    ys = [y for y in range(img.height) if any(px[x, y] < 128 for x in range(blob[0], blob[1] + 1))]
    return (min(ys), max(ys)) if ys else None


def is_wide(img: Image.Image, blob: tuple[int, int]) -> bool:
    """Forma bem mais larga que alta = provavelmente duas letras coladas."""
    rows = ink_rows(img, blob)
    return bool(rows) and (blob[1] - blob[0] + 1) / (rows[1] - rows[0] + 1) > 1.4


def bitmap(img: Image.Image, blob: tuple[int, int]) -> tuple[np.ndarray, float] | None:
    """Recorta a letra (caixa da tinta), normaliza o tamanho. Tinta = 1, fundo = 0."""
    rows = ink_rows(img, blob)
    if not rows:
        return None
    top, bot = rows
    crop = img.crop((blob[0], top, blob[1] + 1, bot + 1)).convert("L")
    aspect = crop.width / crop.height
    arr = 1.0 - np.asarray(crop.resize(SIZE, Image.LANCZOS), dtype=np.float32) / 255.0
    return arr, aspect


class Moldes:
    def __init__(self, path: Path = MOLDES_JSON):
        self.path = path
        self.data: dict[str, list[tuple[float, np.ndarray]]] = {}
        self.dirty = False
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            for letter, variants in raw.items():
                self.data[letter] = [
                    (v["a"], np.array(v["p"], dtype=np.float32).reshape(SIZE[1], SIZE[0]) / 255.0)
                    for v in variants
                ]

    # ---- consulta ----
    def letters_known(self) -> int:
        return sum(1 for k in self.data if len(k) == 1)

    def summary(self) -> str:
        return " ".join(f"{l}{len(v)}" for l, v in sorted(self.data.items())) or "(nenhum)"

    def _distance(self, arr: np.ndarray, aspect: float, mold: tuple[float, np.ndarray]) -> float:
        return float(np.abs(arr - mold[1]).mean()) + ASPECT_W * abs(aspect - mold[0])

    def classify(self, arr: np.ndarray, aspect: float) -> tuple[str | None, float, float]:
        # a chave pode ter mais de uma letra (formas coladas, ex.: "QU")
        """(melhor letra, distância, folga para a 2ª letra diferente)."""
        best: list[tuple[float, str]] = []
        for letter, variants in self.data.items():
            best.append((min(self._distance(arr, aspect, m) for m in variants), letter))
        if not best:
            return None, 9.0, 9.0
        best.sort()
        d1, l1 = best[0]
        d2 = best[1][0] if len(best) > 1 else 9.0
        return l1, d1, d2 - d1

    def read(self, img: Image.Image, blobs: list[tuple[int, int]]) -> str | None:
        """Lê todas as letras pelos moldes; None se alguma não for reconhecida com folga."""
        out = []
        for blob in blobs:
            bm = bitmap(img, blob)
            if bm is None:
                return None
            letter, dist, margin = self.classify(*bm)
            if letter is None or dist > MATCH_MAX or margin < MARGIN:
                return None
            out.append(letter)
        return "".join(out)

    # ---- aprendizado ----
    def assign(self, img: Image.Image, blobs: list[tuple[int, int]], text: str) -> list[str] | None:
        """Distribui as letras de `text` pelas formas. Com 1 forma larga (letras coladas),
        ela recebe as letras que sobram (ex.: QU+E: forma 1 = "QU", forma 2 = "E")."""
        if len(blobs) == len(text):
            return list(text)
        extra = len(text) - len(blobs)
        if extra <= 0:
            return None
        wide = [i for i, b in enumerate(blobs) if is_wide(img, b)]
        if len(wide) != 1:
            return None
        k = wide[0]
        labels = list(text[:k]) + [text[k:k + extra + 1]] + list(text[k + extra + 1:])
        return labels if len(labels) == len(blobs) else None

    def learn(self, img: Image.Image, blobs: list[tuple[int, int]], letters: str) -> int:
        """Guarda os desenhos como moldes das letras dadas. Devolve quantos moldes novos."""
        labels = self.assign(img, blobs, letters)
        if labels is None:
            return 0
        new = 0
        for blob, letter in zip(blobs, labels):
            bm = bitmap(img, blob)
            if bm is None:
                continue
            arr, aspect = bm
            other, dist, _ = self.classify(arr, aspect)
            if other is not None and other != letter and dist < CONFLICT:
                continue  # já parece outra letra: não arrisca contaminar os moldes
            variants = self.data.setdefault(letter, [])
            if len(variants) >= MAX_VARIANTS:
                continue
            if any(self._distance(arr, aspect, m) < DEDUPE for m in variants):
                continue
            variants.append((aspect, arr))
            self.dirty = True
            new += 1
        return new

    def save(self) -> None:
        if not self.dirty:
            return
        raw = {
            letter: [
                {"a": round(a, 4), "p": [int(round(v * 255)) for v in arr.flatten()]}
                for a, arr in variants
            ]
            for letter, variants in sorted(self.data.items())
        }
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        self.dirty = False
