"""
Arquivo de documentos (PDFs) no Supabase, comprimidos e criptografados.

O PDF é encolhido antes de gravar: as imagens são reduzidas para DPI_ALVO no tamanho em que aparecem
na página e regravadas em JPEG (o selo do ASD vai de 3719px/700 KB para ~64 KB e o QR continua legível).
Depois é cifrado com AES-256-GCM (só 28 bytes a mais) e gravado em bytea: a chave do Supabase usada
pela API é a anon, então o banco nunca guarda o documento em claro.
"""
import io
import math
import os
from typing import Any, Dict, List, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv

from app.infrastructure.supabase_db import db

load_dotenv()

TABELA = "crdd_documentos"
DPI_ALVO = 160
QUALIDADE_JPEG = 60
# Images whose placement on the page cannot be read are capped at this width
LARGURA_MAXIMA_PX = 1300


def _chave() -> bytes:
    chave = os.getenv("ENCRYPTION_KEY", "").encode()
    if len(chave) != 32:
        raise RuntimeError("ENCRYPTION_KEY não configurada (precisa ter 32 caracteres)")
    return chave


def cifrar(dados: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_chave()).encrypt(nonce, dados, None)


def decifrar(dados: bytes) -> bytes:
    return AESGCM(_chave()).decrypt(dados[:12], dados[12:], None)


def _larguras_exibidas(page) -> Dict[str, float]:
    """Largura (em pontos) com que cada imagem é desenhada, lendo as matrizes cm/q/Q do conteúdo da página."""
    import pikepdf

    larguras: Dict[str, float] = {}
    pilha, ctm = [], [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    for operandos, operador in pikepdf.parse_content_stream(page):
        op = str(operador)
        if op == "q":
            pilha.append(ctm[:])
        elif op == "Q" and pilha:
            ctm = pilha.pop()
        elif op == "cm" and len(operandos) == 6:
            a, b, c, d, e, f = (float(x) for x in operandos)
            A, B, C, D, E, F = ctm
            ctm = [a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F]
        elif op == "Do" and operandos:
            nome = str(operandos[0])
            larguras[nome] = max(larguras.get(nome, 0.0), math.hypot(ctm[0], ctm[1]))
    return larguras


def comprimir_pdf(pdf: bytes) -> bytes:
    """Devolve o menor entre o PDF original e a versão com imagens reduzidas; nunca falha (no pior caso, o original)."""
    try:
        import pikepdf
        from pikepdf import Name, PdfImage
        from PIL import Image

        documento = pikepdf.open(io.BytesIO(pdf))
        for page in documento.pages:
            larguras = _larguras_exibidas(page)
            for nome, obj in list(page.images.items()):
                imagem = PdfImage(obj).as_pil_image()
                if "/SMask" in obj:
                    # The page background is white, so flattening the transparency onto white looks the same
                    mascara = PdfImage(obj.SMask).as_pil_image().convert("L").resize(imagem.size)
                    fundo = Image.new("RGB", imagem.size, (255, 255, 255))
                    fundo.paste(imagem.convert("RGB"), mask=mascara)
                    imagem = fundo
                else:
                    imagem = imagem.convert("RGB")
                largura_pt = larguras.get(nome)
                alvo = int(largura_pt / 72 * DPI_ALVO) if largura_pt else LARGURA_MAXIMA_PX
                if imagem.width > alvo > 0:
                    imagem = imagem.resize((alvo, max(1, round(imagem.height * alvo / imagem.width))), Image.LANCZOS)
                buffer = io.BytesIO()
                imagem.save(buffer, "JPEG", quality=QUALIDADE_JPEG, optimize=True)
                if len(buffer.getvalue()) >= len(obj.read_raw_bytes()) and "/SMask" not in obj:
                    continue
                obj.write(buffer.getvalue(), filter=Name.DCTDecode)
                obj.Width, obj.Height = imagem.width, imagem.height
                obj.ColorSpace = Name.DeviceRGB
                obj.BitsPerComponent = 8
                for chave in ("/SMask", "/DecodeParms", "/Decode", "/Mask"):
                    if chave in obj:
                        del obj[chave]
        documento.remove_unreferenced_resources()
        saida = io.BytesIO()
        documento.save(saida, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
        menor = saida.getvalue()
        return menor if len(menor) < len(pdf) else pdf
    except Exception as e:
        print(f"[!] [Documentos] Compressão falhou, guardando o original: {type(e).__name__}: {e}")
        return pdf


async def salvar(tipo: str, referencia: str, pdf: bytes, placa: Optional[str] = None,
                 descricao: Optional[str] = None, tamanho_original: Optional[int] = None) -> Dict[str, Any]:
    """Grava o PDF; se tamanho_original vier, o pdf já está comprimido e não é comprimido de novo."""
    if not db.client:
        raise RuntimeError("Supabase não configurado")
    comprimido = pdf if tamanho_original else comprimir_pdf(pdf)
    linha = {
        "tipo": tipo, "referencia": str(referencia), "placa": placa, "descricao": descricao,
        "pdf": "\\x" + cifrar(comprimido).hex(),
        "tamanho_original": tamanho_original or len(pdf), "tamanho": len(comprimido),
    }
    db.client.table(TABELA).upsert(linha, on_conflict="tipo,referencia").execute()
    print(f"[+] [Documentos] {tipo} {referencia}: {linha['tamanho_original']} -> {len(comprimido)} bytes")
    return {k: v for k, v in linha.items() if k != "pdf"}


async def buscar(tipo: str, referencia: str) -> Optional[bytes]:
    if not db.client:
        return None
    resposta = db.client.table(TABELA).select("pdf").eq("tipo", tipo).eq("referencia", str(referencia)).limit(1).execute()
    if not resposta.data:
        return None
    bruto = resposta.data[0]["pdf"]
    return decifrar(bytes.fromhex(bruto[2:] if bruto.startswith("\\x") else bruto))


async def listar(tipo: str = "", placa: str = "", limite: int = 50) -> List[Dict[str, Any]]:
    if not db.client:
        return []
    consulta = db.client.table(TABELA).select("tipo,referencia,placa,descricao,tamanho_original,tamanho,created_at")
    if tipo:
        consulta = consulta.eq("tipo", tipo)
    if placa:
        consulta = consulta.eq("placa", placa.upper())
    return consulta.order("created_at", desc=True).limit(limite).execute().data
