# -*- coding: utf-8 -*-
"""
Proteção de segredos (Access Secret da Tuya, senha da Positivo) com o DPAPI do Windows.

O valor protegido só pode ser aberto pela mesma conta de usuário, no mesmo computador.
Não há alternativa "quase em texto puro": se o DPAPI não estiver disponível, protect() falha
com uma mensagem clara, em vez de gravar o segredo sem proteção.

Formatos gravados:
  "dpapi:<base64>"  protegido (o único formato que o programa grava)
  "b64:<base64>"    legado de versões antigas: só é lido, e é regravado protegido
  sem prefixo       legado em texto puro: só é lido, e é regravado protegido
"""
import base64
import sys

PREFIX = "dpapi:"


class ProtectionError(Exception):
    pass


def is_protected(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def protect(text):
    if not text:
        return ""
    if sys.platform != "win32":
        raise ProtectionError("Não foi possível proteger o segredo: a proteção (DPAPI) só existe no Windows.")
    try:
        return PREFIX + base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode()
    except Exception as e:
        raise ProtectionError(f"O Windows não conseguiu proteger o segredo ({e}). Nada foi salvo.")


def unprotect(value):
    if not value:
        return ""
    if value.startswith(PREFIX):
        if sys.platform != "win32":
            raise ProtectionError("Este segredo foi protegido no Windows e só abre lá.")
        try:
            return _dpapi(base64.b64decode(value[len(PREFIX):]), False).decode("utf-8")
        except Exception:
            raise ProtectionError("Não consegui abrir o segredo salvo (ele foi protegido em outra conta "
                                  "ou outro computador). Cole de novo nos ajustes.")
    if value.startswith("b64:"):                     # legado
        try:
            return base64.b64decode(value[4:]).decode("utf-8")
        except Exception:
            raise ProtectionError("Segredo salvo em formato inválido. Cole de novo nos ajustes.")
    return value                                     # legado em texto puro


def _dpapi(data, encrypt):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp, out = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not fn(ctypes.byref(inp), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError(f"código {ctypes.GetLastError()}")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)
