"""smoke_cross_check.py - une el result.json del emisor con el del receptor.

USO
  python smoke_cross_check.py <result.json emisor> <result.json receptor>

QUÉ HACE
  - Compara el hash de lo enviado (A) con el hash de lo recibido (B).
  - Declara la entrega solo si el receptor la verificó, y la separa de la
    confirmación ARQ del emisor (BUFFER 0).
  - Señala contradicciones: ARQ confirmado pero B sin payload íntegro, o al revés.
  - Guarda cruce.json junto al resultado del receptor.

QUÉ NO HACE
  - No ejecuta ni repite pruebas, ni toca Mercury.
  - No compara tiempos entre PCs: los relojes no están sincronizados en el smoke test.

POR QUÉ
  - BUFFER 0 indica confirmación ARQ, no entrega de aplicación (docs/TNC.md). La prueba
    de entrega es el hash en B; este script impide mezclar ambos conceptos en el informe.
"""
import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    em = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    rx = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    hash_ok = em.get("payload_sha256") is not None and \
        em.get("payload_sha256") == rx.get("payload_sha256")
    avisos = []
    if em.get("arq_confirmado") and not rx.get("entregado_integro"):
        avisos.append("ARQ confirmó (BUFFER 0) pero B no verifica el payload: fallo de entrega")
    if rx.get("entregado_integro") and not em.get("arq_confirmado"):
        avisos.append("B verificó el payload pero A no vio BUFFER 0 (ARQ no observado)")
    if not hash_ok:
        avisos.append("el SHA-256 del emisor y del receptor no coinciden o falta alguno")
    cruce = {
        "tag_emisor": em.get("tag"), "tag_receptor": rx.get("tag"),
        "arq_confirmado_emisor": em.get("arq_confirmado"),
        "entregado_integro_receptor": rx.get("entregado_integro"),
        "hash_coincide_emisor_receptor": hash_ok,
        "fallos_emisor": em.get("fallos"), "fallos_receptor": rx.get("fallos"),
        "avisos": avisos,
        "resultado": "aprobado" if (hash_ok and rx.get("entregado_integro")
                                    and em.get("completado_emisor")) else "fallo",
    }
    salida = Path(sys.argv[2]).with_name("cruce.json")
    salida.write_text(json.dumps(cruce, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(cruce, ensure_ascii=False, indent=2))
    return 0 if cruce["resultado"] == "aprobado" else 1


if __name__ == "__main__":
    sys.exit(main())
