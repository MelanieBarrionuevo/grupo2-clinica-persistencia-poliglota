"""Ejecuta la suite y deja la evidencia de la Fase 5 en docs/evidencias/fase5/.

    python tests/reporte.py              # funcionales + consistencia (no detiene contenedores)
    python tests/reporte.py --fallos     # además P5: caídas y reinicios (detiene y levanta contenedores)

Genera `resultados-AAAAMMDD-HHMM.md` (tabla por prueba, métricas medidas y fallos) y el XML JUnit.
Los argumentos adicionales se pasan tal cual a pytest (ej. `-k p3`).
"""
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "docs" / "evidencias" / "fase5"


def _cmd(*args: str) -> str:
    try:
        r = subprocess.run(args, cwd=RAIZ, capture_output=True, text=True, timeout=60)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _id_prueba(nombre: str) -> str:
    """test_v04_misma_clave… → V-04; test_p3_… → P3; test_f02_… → F-02."""
    partes = nombre.split("[")[0].split("_")
    clave = partes[1] if len(partes) > 1 else ""
    if clave[:1] in ("v", "f") and clave[1:].isdigit():
        return f"{clave[0].upper()}-{clave[1:]}"
    if clave[:1] == "p" and clave[1:].isdigit():
        return clave.upper()
    return "—"


def main() -> int:
    SALIDA.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d-%H%M")
    xml = SALIDA / f"junit-{marca}.xml"
    extra = sys.argv[1:]
    con_fallos = "--fallos" in extra

    inicio = datetime.now()
    codigo = subprocess.call([sys.executable, "-m", "pytest", "tests/", "-v", "-s",
                              f"--junitxml={xml}", "-o", "junit_family=xunit1", *extra],
                             cwd=RAIZ)
    duracion = (datetime.now() - inicio).total_seconds()

    suite = ET.parse(xml).getroot()
    suite = suite if suite.tag == "testsuite" else suite.find("testsuite")
    filas, metricas, fallos = [], [], []
    totales = {"PASÓ": 0, "FALLÓ": 0, "OMITIDA": 0}
    for caso in suite.iter("testcase"):
        nombre = caso.get("name")
        if caso.find("failure") is not None or caso.find("error") is not None:
            resultado = "FALLÓ"
            nodo = caso.find("failure") if caso.find("failure") is not None else caso.find("error")
            fallos.append((nombre, (nodo.get("message") or "")[:400]))
        elif caso.find("skipped") is not None:
            resultado = "OMITIDA"
        else:
            resultado = "PASÓ"
        totales[resultado] += 1
        archivo = (caso.get("classname") or "").split(".")[-1]
        filas.append(f"| {_id_prueba(nombre)} | `{archivo}::{nombre}` | {resultado} | {float(caso.get('time', 0)):.2f} |")
        for prop in caso.iter("property"):
            metricas.append(f"| `{prop.get('name')}` | {prop.get('value')} | `{nombre}` |")

    compose = _cmd("docker", "compose", "ps", "--format", "table {{.Service}}\t{{.Status}}\t{{.Ports}}")
    texto = [
        f"# Resultados de validación · {inicio:%Y-%m-%d %H:%M}",
        "",
        "Generado automáticamente por `python tests/reporte.py"
        + (" --fallos" if con_fallos else "") + "`. No editar a mano: volver a ejecutar.",
        "",
        "| Dato | Valor |",
        "| --- | --- |",
        f"| Commit | `{_cmd('git', 'rev-parse', '--short', 'HEAD') or 'desconocido'}` "
        f"(rama `{_cmd('git', 'rev-parse', '--abbrev-ref', 'HEAD') or '?'}`) |",
        f"| Equipo | {platform.system()} {platform.release()} · Python {platform.python_version()} |",
        f"| Docker | {_cmd('docker', 'version', '--format', '{{.Server.Version}}') or 'no disponible'} |",
        f"| Pruebas de fallos (P5) | {'incluidas' if con_fallos else 'no incluidas (usar --fallos)'} |",
        f"| Duración total | {duracion:.0f} s |",
        f"| Resultado | **{totales['PASÓ']} pasaron · {totales['FALLÓ']} fallaron · {totales['OMITIDA']} omitidas** |",
        "",
    ]
    if len(compose.splitlines()) > 1:
        texto += ["## Contenedores durante la prueba", "", "```", compose, "```", ""]
    if metricas:
        texto += ["## Métricas medidas", "", "| Métrica | Valor | Prueba |", "| --- | --- | --- |", *metricas, ""]
    texto += ["## Detalle por prueba", "", "| ID | Prueba | Resultado | Segundos |", "| --- | --- | --- | --- |", *filas, ""]
    if fallos:
        texto += ["## Fallos", ""] + [f"- `{n}`: {m}" for n, m in fallos] + [""]

    informe = SALIDA / f"resultados-{marca}.md"
    informe.write_text("\n".join(texto), encoding="utf-8")
    print(f"\nInforme: {informe.relative_to(RAIZ)}")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
