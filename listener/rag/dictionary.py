"""F5.10 (Escopo 2): o dicionário de dados que a LLM recebe (o prefixo em cache das rodadas), gerado do código: cada visão de
`rag/semantic.py` com a descrição, cada coluna com o tipo, a unidade, os valores possíveis e as armadilhas, e exemplos de
SQL. Os testes conferem que toda coluna está descrita, que todo exemplo roda na sandbox e que toda tabela do arquivo está
classificada: visível por uma visão (as dependências que a sandbox grava), interna do FTS ou negada (`DENIED`, com o porquê).
Texto em PT-BR, sem Qt e sem a camada da LLM."""
from __future__ import annotations

import json
from dataclasses import dataclass

TYPES = {"i": "inteiro", "t": "texto", "d": "decimal"}
NAME_HINT = "o nome; compare com LIKE ? e o valor 'Ana%' (o LIKE ignora maiúsculas, não acentos)"


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    kind: str                         # i, t ou d (`TYPES`)
    text: str                         # o que é, a unidade, os valores e as armadilhas

    def render(self) -> str:
        return f"  {self.name} ({TYPES[self.kind]}): {self.text}"


@dataclass(frozen=True, slots=True)
class View:
    name: str
    text: str
    columns: tuple[Column, ...]
    notes: tuple[str, ...] = ()

    def render(self) -> str:
        return "\n".join([f"{self.name}: {self.text}", *(c.render() for c in self.columns), *(f"  Atenção: {n}" for n in self.notes)])


def _c(name: str, kind: str, text: str) -> Column:
    return Column(name, kind, text)


ID = "a reunião; cite como [R<reuniao_id>] (com o mm:ss quando houver)"
START = "o momento desde o começo da reunião, mm:ss (para citar [R<reuniao_id> mm:ss]); nulo sem momento"
START_MS = "o mesmo momento em ms (para ordenar e comparar)"
VIEWS: tuple[View, ...] = (
    View("agora", "uma linha só: o agora do computador do usuário, no fuso dele (use para 'hoje', 'este mês', 'atrasada')", (
        _c("hoje", "t", "AAAA-MM-DD"), _c("hora", "t", "HH:MM, 24 h"),
        _c("dia_semana", "t", "segunda, terça, quarta, quinta, sexta, sábado ou domingo"), _c("fuso", "t", "o deslocamento, ex. -03:00"))),
    View("reunioes", "uma linha por reunião gravada (só as dos filtros da aba Reuniões, quando há)", (
        _c("reuniao_id", "i", ID), _c("titulo", "t", "o título do usuário ou, sem ele, o sugerido pela LLM; nulo sem título"),
        _c("dia", "t", "AAAA-MM-DD local do início; compare com dia BETWEEN ? AND ?"), _c("hora", "t", "HH:MM local do início"),
        _c("mes", "t", "AAAA-MM local"), _c("dia_semana", "t", "segunda, terça, quarta, quinta, sexta, sábado ou domingo"),
        _c("duracao_min", "d", "minutos gravados; nulo enquanto grava"),
        _c("estado", "t", "gravando, pausada, gravada (sem processamento), cancelada, interrompida, processada (falantes a revisar) "
                          "ou revisada"),
        _c("plataforma", "t", "o app gravado (ex. ms-teams.exe); nulo quando foi o som do sistema inteiro"),
        _c("presencial", "i", "1 = sala de reunião presencial, 0 = online"), _c("idioma", "t", "pt ou en; nulo = o padrão"),
        _c("participantes_previstos", "t", "como o usuário digitou antes de gravar: quem foi convidado, não quem falou"),
        _c("tem_falas", "i", "1 = processada, com falas; 0 = sem processamento (sem falas nem participação)"),
        _c("tem_ata", "i", "1 = tem ata; a ata só existe quando o usuário pede")),
        ("o dia é o local: nunca use date() nem datetime() sobre ele (convertem para UTC)",)),
    View("pessoas", "as pessoas cadastradas (sem e-mail nem notas)", (
        _c("pessoa_id", "i", "a pessoa"), _c("nome", "t", NAME_HINT), _c("cargo", "t", "o cargo atual da ficha; nulo sem cargo"),
        _c("empresa", "t", "nulo sem empresa"), _c("eu", "i", "1 = o próprio usuário (quem fala no microfone)"))),
    View("participacao", "quem falou em cada reunião processada: uma linha por falante, canal e cargo do dia", (
        _c("reuniao_id", "i", ID), _c("pessoa_id", "i", "a pessoa; nulo para voz sem nome"),
        _c("falante", "t", "o nome; voz sem nome aparece como 'Voz 2' (é um nome: copie, não traduza)"),
        _c("cargo", "t", "o cargo naquele dia; nulo sem cargo"), _c("canal", "t", "L = microfone do usuário, R = os outros"),
        _c("falas", "i", "quantas falas"), _c("fala_min", "d", "minutos falados"), _c("primeira_fala", "t", "mm:ss da primeira fala")),
        ("sem processamento não há participação: veja reunioes.tem_falas",)),
    View("falas", "a transcrição vigente (a do último processamento), uma linha por fala; são muitas: agregue no SQL", (
        _c("fala_id", "i", "a fala"), _c("reuniao_id", "i", ID), _c("inicio", "t", START), _c("inicio_ms", "i", START_MS),
        _c("fim_ms", "i", "o fim da fala, em ms"), _c("falante", "t", "como em participacao.falante"),
        _c("pessoa_id", "i", "nulo para voz sem nome"), _c("cargo", "t", "o cargo naquele dia; nulo sem cargo"),
        _c("canal", "t", "L = microfone do usuário, R = os outros"),
        _c("texto", "t", "a transcrição revisada pela LLM quando existe, senão a original")),
        ("para achar palavras use trechos com MATCH (LIKE em falas varre tudo e pode passar do prazo)",)),
    View("marcadores", "os marcadores que o usuário pôs durante a gravação", (
        _c("marcador_id", "i", "o marcador"), _c("reuniao_id", "i", ID), _c("inicio", "t", START), _c("inicio_ms", "i", START_MS),
        _c("nota", "t", "o que o usuário escreveu; nulo sem nota"))),
    View("acoes", "as ações da ata atual de cada reunião", (
        _c("acao_id", "i", "a ação"), _c("reuniao_id", "i", ID), _c("texto", "t", "a ação"),
        _c("dono", "t", "o nome da pessoa cadastrada ou o texto da ata; nulo sem dono"),
        _c("dono_pessoa_id", "i", "nulo quando o dono não casou com uma pessoa"),
        _c("prazo", "t", "AAAA-MM-DD; nulo sem prazo; atrasada = prazo < agora.hoje com estado 'aberta'"),
        _c("estado", "t", "aberta, feita ou descartada"), _c("inicio", "t", START), _c("inicio_ms", "i", START_MS),
        _c("fala_id", "i", "a fala que é a evidência; nulo sem evidência")),
        ("a ata só existe quando o usuário pede: reunião sem ata não tem ações",)),
    View("ata_itens", "os itens da ata atual de cada reunião", (
        _c("reuniao_id", "i", ID),
        _c("tipo", "t", "ata (o contexto), decisao, destaque, pendencia ou proximo_passo (as ações estão em acoes)"),
        _c("texto", "t", "o item"), _c("inicio", "t", START), _c("inicio_ms", "i", START_MS)),
        ("vem do índice da busca: uma ata recém-gerada aparece depois de indexada; a ata inteira está em resumos",)),
    View("resumos", "os textos gerados da reunião", (
        _c("reuniao_id", "i", ID),
        _c("tipo", "t", "ata (a ata inteira, em Markdown), executivo, pre_resumo ou parcial (feito durante a gravação)"),
        _c("texto", "t", "o editado pelo usuário quando há"), _c("idioma", "t", "o idioma do texto"))),
    View("trechos", "a transcrição e a ata em trechos, para procurar palavras com MATCH", (
        _c("trecho_id", "i", "o trecho"), _c("reuniao_id", "i", ID),
        _c("tipo", "t", "transcricao, decisao, destaque, pendencia, proximo_passo, ata, acao, executivo ou reuniao"),
        _c("inicio", "t", START), _c("inicio_ms", "i", START_MS), _c("falantes", "t", "quem fala no trecho"),
        _c("texto", "t", "o trecho"),
        _c("busca", "t", "só para WHERE busca MATCH ?: palavras sem acento, OR entre termos, aspas para frase, * para prefixo"),
        _c("relevancia", "d", "só com MATCH: menor = mais relevante (ORDER BY relevancia)")),
        (("o MATCH vai direto na consulta sobre trechos; com o erro 'unable to use function MATCH', filtre antes numa CTE: "
         "WITH t AS (SELECT * FROM trechos WHERE busca MATCH ?) SELECT ... FROM t"),)),
    View("custos_llm", "cada chamada paga à LLM", (
        _c("chamada_id", "i", "a chamada"), _c("reuniao_id", "i", "nulo = fora de reunião (chat, busca)"),
        _c("dia", "t", "AAAA-MM-DD local"), _c("mes", "t", "AAAA-MM local"),
        _c("tarefa", "t", "identify, minutes, executive, pre_summary, title, revise, glossary, transcribe, chat, partial, rag_embed, "
                          "rag_chat, rag_sql, rag_answer ou test"),
        _c("provedor", "t", "gemini, openai, anthropic ou local"), _c("modelo", "t", "o modelo"),
        _c("tokens_entrada", "i", "tokens"), _c("tokens_saida", "i", "tokens"), _c("tokens_cache", "i", "tokens lidos do cache"),
        _c("custo_usd", "d", "dólares; nulo = sem preço cadastrado"), _c("status", "t", "ok ou erro"))),
)
# (pergunta, SQL, params): o teste roda todos na sandbox
EXAMPLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Quantas reuniões tive com a Ana em outubro?",
     ("SELECT count(DISTINCT p.reuniao_id) AS reunioes FROM participacao p JOIN reunioes r USING (reuniao_id) "
     "WHERE p.falante LIKE ? AND r.mes = ?"), ("Ana%", "2026-10")),
    ("Ações atrasadas por dono",
     ("SELECT coalesce(a.dono, '(sem dono)') AS dono, count(*) AS atrasadas FROM acoes a, agora g WHERE a.estado = 'aberta' "
     "AND a.prazo < g.hoje GROUP BY 1 ORDER BY 2 DESC"), ()),
    ("Onde falamos de orçamento?",
     "SELECT reuniao_id, inicio, inicio_ms, falantes, texto FROM trechos WHERE busca MATCH ? ORDER BY relevancia LIMIT 20",
     ("orcamento",)),
    ("Quem mais falou na reunião 12?",
     "SELECT falante, fala_min FROM participacao WHERE reuniao_id = CAST(? AS INTEGER) ORDER BY fala_min DESC", ("12",)),
    ("Reuniões com mais de 3 falantes", ("SELECT reuniao_id, count(*) AS falantes FROM participacao GROUP BY reuniao_id "
     "HAVING count(*) > CAST(? AS INTEGER)"), ("3",)),
    ("Quanto gastei com a LLM este mês?", ("SELECT round(sum(c.custo_usd), 4) AS usd, count(*) AS chamadas FROM custos_llm c, agora g "
     "WHERE c.mes = substr(g.hoje, 1, 7)"), ()),
    ("O que foi decidido na última reunião com ata?", ("SELECT i.reuniao_id, i.inicio, i.texto FROM ata_itens i WHERE i.tipo = 'decisao' "
     "AND i.reuniao_id = (SELECT max(reuniao_id) FROM reunioes WHERE tem_ata = 1)"), ()),
)
# tabelas do arquivo que nenhuma visão lê, e por quê (o teste confere que toda tabela está aqui, numa visão ou no FTS)
DENIED: dict[str, str] = {
    "settings": "configurações", "presets": "presets de gravação", "providers": "chaves de API (CP10)",
    "meetings": "lida pelo app para montar reunioes, sem as notas", "events": "eventos do gravador e da fila",
    "revisions": "histórico de edições", "captions": "legendas ao vivo (a transcrição final está em falas)",
    "voice_profiles": "perfis de voz", "voice_embeddings": "vetores de voz", "rag_vectors": "vetores da busca por sentido",
    "rag_models": "modelos da busca por sentido", "processing_jobs": "a fila (o job vigente entra pronto nas visões)",
    "clusters": "as vozes (o falante entra pronto)", "job_artifacts": "artefatos da fila", "chat_messages": "as conversas",
    "chat_threads": "as conversas", "llm_prices": "a tabela de preços", "schema_migrations": "versões do banco",
    "sqlite_sequence": "interna do SQLite",
}
FTS_INTERNAL = frozenset({"rag_fts_data", "rag_fts_idx", "rag_fts_docsize", "rag_fts_config"})
HEADER = ("Visões da base do usuário, só leitura (as únicas tabelas que existem). Datas em AAAA-MM-DD e horas locais; tempos "
          "dentro da reunião em mm:ss e ms; nulo = sem o dado.")


def view(name: str) -> View:
    return next(v for v in VIEWS if v.name == name)


def render() -> str:
    """O dicionário inteiro, como a LLM o recebe (estável: entra no prefixo em cache)."""
    examples = "\n".join(f"-- {q}\n{sql};  params: {json.dumps(list(p), ensure_ascii=False)}" for q, sql, p in EXAMPLES)
    return f"{HEADER}\n\n" + "\n\n".join(v.render() for v in VIEWS) + f"\n\nExemplos:\n{examples}"


__all__ = ["DENIED", "EXAMPLES", "FTS_INTERNAL", "VIEWS", "Column", "View", "render", "view"]
