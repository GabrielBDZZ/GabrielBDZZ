"""Corrige a pizza de linguagens e o eixo "Commit" do gráfico 3D.

A API de contribuições do GitHub esconde em quais repositórios privados estão as
contribuições (até para o próprio dono), então a action só enxerga os públicos.
Este script conta os commits do último ano pelo histórico de cada repositório
(que enxerga os privados) e redesenha esses dois elementos nos SVGs gerados.

Uso:
    python3 linguagens_3d.py <pasta-dos-svgs> [--dados nodes.json]

Variáveis de ambiente:
    GH_TOKEN       PAT classic com escopo "repo" (obrigatório sem --dados)
    COMMIT_EMAILS  e-mails dos seus commits, separados por vírgula (opcional;
                   sem ele, conta só os commits atribuídos à sua conta)

O log só mostra totais por linguagem, nunca nomes de repositórios.
"""

import glob
import json
import math
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

SVG_NS = "http://www.w3.org/2000/svg"
NS = "{%s}" % SVG_NS
OTHER_NAME = "other"
OTHER_COLOR = "#444444"
MAX_LANGS = 5

QUERY = """
query($since: GitTimestamp!, $author: CommitAuthor!, $cursor: String) {
  viewer {
    repositories(first: 100, after: $cursor,
                 affiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER],
                 ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        isFork
        primaryLanguage { name color }
        defaultBranchRef { target { ... on Commit { history(since: $since, author: $author) { totalCount } } } }
      }
    }
  }
}
"""


def graphql(token, query, variables):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.load(resp)
    if body.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "?") for e in body["errors"]))
    return body["data"]


def buscar_repositorios(token, emails):
    if emails:
        author = {"emails": emails}
    else:
        author = {"id": graphql(token, "{ viewer { id } }", {})["viewer"]["id"]}
    since = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    nodes, cursor = [], None
    while True:
        data = graphql(token, QUERY, {"since": since, "author": author, "cursor": cursor})
        repos = data["viewer"]["repositories"]
        nodes.extend(repos["nodes"])
        if not repos["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = repos["pageInfo"]["endCursor"]


def agregar(nodes):
    """Soma os commits por linguagem principal; devolve (top linguagens + other, total)."""
    por_lingua, total = {}, 0
    for repo in nodes:
        if repo.get("isFork") or not repo.get("defaultBranchRef"):
            continue
        n = repo["defaultBranchRef"]["target"]["history"]["totalCount"]
        if not n:
            continue
        total += n
        lang = repo.get("primaryLanguage")
        if not lang:
            continue  # sem linguagem (ex.: repositório só com README) vai para "other"
        info = por_lingua.setdefault(lang["name"], {"name": lang["name"], "color": lang["color"] or OTHER_COLOR, "n": 0})
        info["n"] += n
    langs = sorted(por_lingua.values(), key=lambda d: -d["n"])[:MAX_LANGS]
    resto = total - sum(d["n"] for d in langs)
    if resto > 0:
        langs.append({"name": OTHER_NAME, "color": OTHER_COLOR, "n": resto})
    return langs, total


def fmt(v):
    """Mesmo arredondamento da action (2 casas, sem zeros sobrando)."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def arco(cx, cy, r_out, r_in, a0, a1):
    """Fatia de donut entre os ângulos a0 e a1 (radianos, 0 = topo, sentido horário)."""
    if a1 - a0 >= 2 * math.pi - 1e-9:
        a1 = a0 + 2 * math.pi - 1e-4  # fatia única: evita arco degenerado
    p = lambda r, a: (cx + r * math.sin(a), cy - r * math.cos(a))
    large = 1 if a1 - a0 > math.pi else 0
    (x0, y0), (x1, y1) = p(r_out, a0), p(r_out, a1)
    (x2, y2), (x3, y3) = p(r_in, a1), p(r_in, a0)
    return (f"M{fmt(x0)},{fmt(y0)}A{fmt(r_out)},{fmt(r_out)},0,{large},1,{fmt(x1)},{fmt(y1)}"
            f"L{fmt(x2)},{fmt(y2)}A{fmt(r_in)},{fmt(r_in)},0,{large},0,{fmt(x3)},{fmt(y3)}Z")


def desenhar_pizza(grupo, langs):
    """Recria o conteúdo do grupo da pizza no mesmo layout da action (create-pie-language.ts)."""
    pie_g = next(g for g in grupo.iter(NS + "g") if g.find(NS + "path") is not None)
    radius = float(re.findall(r"[-\d.]+", pie_g.get("transform"))[0])
    height = radius * 2
    margin = radius / 10
    row = 8
    offset = (row - len(langs)) / 2 + 0.5
    font = height / row / 1.5

    for filho in list(grupo):
        grupo.remove(filho)

    legenda = ET.SubElement(grupo, NS + "g", transform=f"translate({fmt(radius * 2.1)}, 0)")
    for i, lang in enumerate(langs):
        ET.SubElement(legenda, NS + "rect", {
            "x": "0", "y": fmt((i + offset) * (height / row) - font / 2),
            "width": fmt(font), "height": fmt(font), "fill": lang["color"],
            "class": "stroke-bg", "stroke-width": "1px"})
    for i, lang in enumerate(langs):
        t = ET.SubElement(legenda, NS + "text", {
            "dominant-baseline": "middle", "x": fmt(font * 1.2),
            "y": fmt((i + offset) * (height / row)), "class": "fill-fg", "font-size": f"{fmt(font)}px"})
        t.text = lang["name"]

    fatias = ET.SubElement(grupo, NS + "g", transform=f"translate({fmt(radius)}, {fmt(radius)})")
    soma = sum(l["n"] for l in langs) or 1
    a = 0.0
    for lang in langs:
        b = a + 2 * math.pi * lang["n"] / soma
        path = ET.SubElement(fatias, NS + "path", {
            "d": arco(0, 0, radius - margin, radius / 2, a, b),
            "class": "stroke-bg", "stroke-width": "2px", "style": f"fill: {lang['color']};"})
        ET.SubElement(path, NS + "title").text = f"{lang['name']} {lang['n']}"
        a = b


def nivel(valor):
    """Escala logarítmica do radar (toLevel em create-radar-contrib.ts)."""
    return 0.8 if valor < 1 else min(math.log10(valor), 5) + 1


def corrigir_radar(grupo, commits):
    """Move o vértice "Commit" (o primeiro eixo, apontando para cima) para o total real."""
    poly = grupo.find(NS + "polygon")
    top_label = [t for t in grupo.findall(NS + "text") if t.get("class") == "fill-weak"]
    radius = -float(top_label[-1].get("y"))  # o rótulo "10K" fica em y = -radius
    vertice = f"0,{fmt(-radius * nivel(commits) / 5)}"
    troca = lambda pts: " ".join([vertice] + pts.split()[1:])
    poly.set("points", troca(poly.get("points")))
    anim = poly.find(NS + "animate")
    if anim is not None:
        inicio, fim = anim.get("values").split(";")
        anim.set("values", f"{inicio};{troca(fim)}")
    for eixo in grupo.findall(NS + "g"):
        titulo = eixo.find(NS + "text/" + NS + "title")
        if titulo is not None and eixo.find(NS + "text").text == "Commit":
            titulo.text = str(commits)


def corrigir_svg(caminho, langs, commits):
    ET.register_namespace("", SVG_NS)
    arvore = ET.parse(caminho)
    raiz = arvore.getroot()
    pizza = radar = None
    for g in raiz.findall(NS + "g"):
        if g.find(".//" + NS + "polygon[@class='radar']") is not None:
            radar = g
        elif g.find(".//" + NS + "path[@class='stroke-bg']") is not None:
            pizza = g
    if pizza is None or radar is None:
        print(f"::warning::{os.path.basename(caminho)}: layout inesperado, mantido como está")
        return False
    desenhar_pizza(pizza, langs)
    corrigir_radar(radar, commits)
    arvore.write(caminho, encoding="unicode", xml_declaration=False)
    return True


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    pasta = args[0]
    if "--dados" in args:
        with open(args[args.index("--dados") + 1], encoding="utf8") as f:
            nodes = json.load(f)
    else:
        token = os.environ.get("GH_TOKEN", "").strip()
        if not token:
            print("::warning::GH_TOKEN vazio: a pizza fica só com os repositórios públicos.")
            return
        emails = [e.strip() for e in re.split(r"[,\s]+", os.environ.get("COMMIT_EMAILS", "")) if e.strip()]
        try:
            nodes = buscar_repositorios(token, emails)
        except Exception as erro:  # token vencido, rede etc.: não derruba o resto do job
            print(f"::warning::Não foi possível ler o histórico ({erro}); SVGs mantidos como estão.")
            return

    langs, total = agregar(nodes)
    if not total:
        print("::warning::Nenhum commit encontrado no último ano; SVGs mantidos como estão.")
        return
    print(f"Commits no último ano: {total}")
    for lang in langs:
        print(f"  {lang['name']:<14} {lang['n']:>5}  {100 * lang['n'] / total:5.1f}%")

    arquivos = sorted(glob.glob(os.path.join(pasta, "profile-*.svg")))
    ok = sum(corrigir_svg(a, langs, total) for a in arquivos)
    print(f"SVGs corrigidos: {ok}/{len(arquivos)}")


if __name__ == "__main__":
    main()
