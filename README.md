# Bomb Party BR bot

Script em Python que lê a sílaba na tela (OCR), escolhe a maior palavra do dicionário
que a contém e digita com velocidade variável. Só age quando detecta que é a sua vez.

> Uso por sua conta e risco: automatizar jogo online pode violar as regras da sala/site.

## Requisitos
- Windows, Python 3.10+
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) em `C:\Program Files\Tesseract-OCR`
- `pip install -r requirements.txt`

## Uso
```bash
python bombparty_bot.py --calibrar   # 1ª vez: marca a sílaba e a caixa de digitação (na sua vez)
python bombparty_bot.py              # roda o bot
```
Teclas: **F8** liga/desliga · **F9** zera palavras usadas · **ESC** sai.

Opções: `--wpm-min`, `--wpm-max`, `--long-len`, `--max-len`, `--dict`, `--debug`.

## Dicionário
As palavras ficam em `bomb-party-br.html` (array `DICTIONARY`); abra o arquivo no navegador
para consultar por sílaba.

## Logs (não versionados)
- `bot.log`: cada sílaba lida e o que aconteceu (digitou, faltando, esgotada, recusada, ocr_falha)
- `silabas_faltando.json`: sílabas sem nenhuma palavra no dicionário, com contagem
- `ocr_falhas/`: recortes em que o OCR não conseguiu ler
