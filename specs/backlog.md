# Backlog — estado do projeto e o que fazer agora

Quando ler: sempre (T0). Único lugar de estado; atualize ao fim de cada sessão. Tamanho-alvo: 5 KB.

## Onde paramos (02/11/2026, Windows nativo; tudo commitado; o push é do dono)
- Código: F1 a F5.11 implementadas (o detalhe na seção Implementação de cada spec; as decisões em `decisoes.md`); F6 é
  rascunho inicial e F7 é futuro. Suíte de 1.438 testes, esquema v9. Nenhum gate medido ainda.
- **F5.11 implementada** (02/11, D-077, D-078, `features/F5.11-revisao-reuniao-e-linguagem.md`): as 23 melhorias da
  crítica de UX (U-098 a U-120) em nove commits, de `8655ef0` a `7aee204`, com a revisão do diff corrigida; a nova
  crítica deu 34/40, sem P1 (era 28). Falta o dono: a Parte 22 do roteiro. O achado de produção dela, o Enter que
  apagava a gravação na confirmação do descarte, saiu antes, na D-076.
- **F5.12 aprovada, em implementação** (02/11, D-079, `features/F5.12-sobre-e-apoio.md`): o Sobre (bandeja,
  Configurações e assistente) e o convite para apoiar com R$ 0,99 pelo Pix ou US$ 0,20 pelo PayPal (U-121 a U-128).
- Já rodou de verdade no Windows [fato]: a captura L e R, o ao vivo, o Whisper, o pyannote e o adaptador do Gemini
  (reunião 8, Teams, 15 min, pelo `.exe`, 30/10; números em `decisoes.md`, Medições). GPT e Claude seguem só contra
  transporte simulado.
- O `.exe` que vale para as Partes 19 a 22 é o de 02/11, 00:22, já com a F5.11 (541 s, 892 MB, sonda `--probe` ok).
- CI: o `ci.yml` só ficou válido na D-072; o push de 31/10 é o primeiro run de verdade. Conferir no Actions se passou
  nos três sistemas (pode mostrar falhas de Linux e macOS nunca vistas).
- Próximo passo do dono: "Verificar no Windows", na ordem. Ele devolve o que cada parte do roteiro pede; a LLM registra
  os números em `decisoes.md`, marca aqui e corrige o que aparecer (a LLM roda sozinha o que não depende dele, 25/10).
  Sem o dono, o próximo trabalho depende da pergunta 7.

## Como retomar (5 minutos)
1. T0: `AGENTS.md` (primeiro: em que ambiente a sessão está), `specs/01-clausulas.md` e este arquivo; depois só a
   camada da tarefa.
2. Testes (D-050): `python -m pytest -q` é a parte rápida; antes de commitar, a inteira (`--tudo`, a que o CI roda),
   mais `ruff check .`, `mypy` e `mypy --platform linux`. No Windows, pelo venv do checkout:
   `.venv\Scripts\python.exe -m pytest -q --tudo`, `.venv\Scripts\ruff.exe check .` e `.venv\Scripts\mypy.exe`.
3. Antes de código: a spec da entrega em `specs/features/` está `aprovada`? (CP13)

## Verificar no Windows (na ordem, quando a sessão estiver no Windows nativo)
Tudo que foi feito no Linux ou no WSL só vale depois daqui (CP15). Cada item é do dono: o passo a passo é a parte
indicada de `specs/roteiro-teste-windows.md`; gravar reunião e usar chave pedem o OK dele. Números em `decisoes.md`
(Medições) e `memory/maquina.md`; defeito vira correção pequena (sem agente) ou item novo aqui.
1. [ ] F5.11 (Parte 22): a revisão de vozes, a reunião aberta e a linguagem numa reunião real; o "Testar" do token
   com a rede da empresa; nenhum `listener --hf-check` sobrando.
2. [ ] F5.10, o gate G5.10 (Parte 21): as 15 perguntas com o Gemini no banco real.
3. [ ] F5.9, os canais que se recuperam (Parte 20): na reunião 8 a troca manual do R aos 13,5 s encerrou a volta ao app
   (D-067), e as seis verificações (app que fecha no meio, chamada que entra depois, microfone atrasado, "agora X",
   onde a chamada toca, eco) seguem sem resultado.
4. [ ] D-067, o som que segue o aparelho escolhido (Parte 18).
5. [ ] F5.8, o `.exe` numa reunião de verdade (Parte 19).
6. [ ] F5.7, o aviso de reunião sem gravação (Parte 17; reinicie o app antes).
7. [ ] F5.6: L2 (Parte 16; reinicie o app antes), L1 (Parte 15) e L3 (Parte 14). Junto da L1, a D-071: a ata em pt-BR
   segue o contrato como seguia em inglês (evidência nos itens, `[mm:ss]` no chat, "Voz 2" sem tradução); se piorar, o
   `prompt_version` das chamadas diz qual texto foi usado.
8. [ ] F5.5 (Parte 13), F5.4 (Parte 12: claro, escuro, contraste alto, notebook, teclado), F5.3 (Parte 11: idioma,
   fila, aviso, fone no meio da gravação) e F5.2 (Parte 10: fone conectado com o app aberto, ficha da pessoa, ata
   depois da identificação, log em DEBUG).
9. [ ] Uso real (B1; Partes 3 e 4): a reunião 8 cobriu a gravação e o processamento; faltam a revisão de falantes, a
   reunião aberta e os logs que a Parte 3 pede (tempo e RAM por etapa, sonda Q8, canário). Depois da Parte 3, apagar o
   venv do F0 e as duas entradas vazias de 24/10 no cache do Hugging Face (D-050, D-052).
10. [ ] Gemini, busca local e ao vivo (Partes 5, 6 e 7; B15): busca e perguntas com o Gemini (F5), a busca local sem
   custo (F5.1) e o ao vivo com nomes (F4).
11. [ ] Reuniões reais e bordas (Partes 8 e 9): G1 (B2), G2, G3, G4 e G5; a D-072, o logoff cancelado, e a D-076 (o
    ✕ do widget com Enter na confirmação: a gravação segue) entram na Parte 9; a metade Linux do L0 da F5.1, na
    próxima sessão no Linux.

Concluídos: checkout, venv e suíte no Windows (25/10; pelo lock desde 26/10, D-050); Parte 1 (26/10); Parte 2, as
chaves (o Gemini 3 passou no "Testar conexão" em 26/10, D-055; o pyannote diarizou a reunião 8 com o token em 30/10).

## Perguntas abertas ao dono (padrão entre parênteses; só vale com a resposta dele)
As respondidas (2 a 4, 11 a 16 e 18) saíram daqui: o registro está em `decisoes.md`, nas specs e no git.
1. P23, RAM da busca local: o tokenizer do e5 custa ~262 MB e o app vai a ~620 a 670 MB com o Histórico aberto
   (aceitar e medir na Parte 6; a alternativa é o `sentencepiece`, dependência nova).
5. `AGENTS.md`: "Testes: `python -m pytest -q` (a rápida); antes de commitar, `--tudo` (D-050)" (sim).
6. `pytest-xdist`, só de desenvolvimento, para a meta de 8 s (sim, num lote pequeno).
7. Próximo passo sem o dono: detalhar a spec da F6 e passá-la pela revisão de arquitetura para ele aprovar (sim).
8. Sem o token do Hugging Face, transcrever mesmo assim, sem saber quem fala no R (o L é você), em vez de o
   processamento não sair (sim; muda o comportamento da F2, vai para uma spec).
9. As janelas pretas eram os workers (D-056), e fechar uma delas no X mata o worker sem rastro (medido). Foi isso nas
   quedas do job da reunião 2 (26/10, 15:35 e 15:36) e na do terceiro teste (17:27:55)? E o app que fechou sem o "app
   encerrado": foi o terminal dele que se fechou? (sim aos dois; se não, investigar o antivírus/EDR).
10. Além das janelas pretas (D-056) e da revisão (U-039), alguma outra janela ainda sobe por cima? (nenhuma).
17. F5.8: uma trava de instância única, para o `.exe` aberto duas vezes, ou junto com o venv, no mesmo banco (não
    agora; uma spec pequena quando você quiser).
19. A pasta do banco do teste de 26/10 (`C:\dev\base_listener_meet`) apareceu vazia às 14:21 daquele dia, fora da
    Lixeira e sem nenhum processo da LLM nela; os `rmtree` do código e dos testes só alcançam pastas do `mkdtemp`. Foi
    você ou outra ferramenta? (sim, por fora do app; se não, investigar).

## Bloqueios que só o dono destrava
| # | O quê | Destrava |
| --- | --- | --- |
| B1 | uso real do app pelo roteiro (Partes 3 e 4): gravação, fila, revisão, reunião aberta; dizer o que está estranho | F1.1, F1.2 e o resto da F2 e da F3 pelo uso de verdade |
| B2 | gate G1: 3 reuniões reais com headset em 2 plataformas e `python -m listener --check <pasta do banco>` (já imprime deriva e deslocamento L/R) | fecha F1 e F1.1; dá áudio real para o G2 |
| B5 | gravação real de ~1 h com headset (ou as do G1); a reunião 8 deu a primeira medida, de 15 min | Whisper e pyannote por hora de áudio |
| B15 | `python scripts/live_probe.py all <gravação real>` com `HF_TOKEN` (o embedding do wespeaker) e uma gravação com a legenda ligada | os nomes ao vivo, o EcoQoS e o G4 (o filho real já legendou a reunião 8) |

Fechados: B3 (D-036) · B4 (o token do Hugging Face está no banco real: o pyannote diarizou a reunião 8, 30/10) · B6 ·
B7 (D-034) · B8 (D-035) · B9 (D-031) · B10 (D-039) · B11 (D-041) · B12 (D-043) · B13 (a chave do Gemini passou no
"Testar conexão", 26/10, D-055) · B14 (D-044) · B16 (D-047).

## Dívidas técnicas
- D-072, reais e pequenas (sem correção por ora): a morte da thread de trabalho da captura não marca a reunião; o
  "banco não responde há N s" exagera (`pending_since`); corridas de leitura e escrita fora da transação (sugestão da
  LLM num cluster recém-confirmado, ação superada que volta, `upsert_me`); o chat em streaming sem retentativa antes do
  primeiro pedaço e o cancelado gravado como `OK` sem custo; o preparo do ao vivo sem prazo no `repo_info` e sem
  Cancelar; o `db-location.json` sem gravação atômica; "Apagar preset" sem confirmação (UX: perguntar ao dono); o
  assistente diz "cinco passos" e mostra seis.
- F5.10 (D-075): o `--downgrade` com o app aberto faz o chat falhar até reabrir (nada se perde); a `memory_rss` do
  Windows mede o working set, que o sistema pode encolher, e o teto da consulta mediria menos que o real.
- F5.9, das revisões: `test_call_watch.py::test_failures_degrade_with_one_warning_and_recover_with_one_info` é instável
  na suíte inteira (3 falhas em 7; 9/9 isolado); o `pyproject` não liga o `C901` (o limite de complexidade 10 é só
  convenção); o nativo do proctap cai no sistema inteiro calado em caminhos de falha (`is_process_specific()` diz
  quando) e somaria o sistema inteiro no canal do app sem aviso; o pedido de saída ambíguo (nome repetido no loopback)
  não fica no handle, e a vigia pode pedir `back` de novo a cada mudança de assinatura.
- F2 (Q7), a lease lida só entre janelas: a janela de diarização de 1.200 s (RTF 1,2 a 1,6) atravessa uma gravação
  inteira (o job 47 diarizou durante a reunião 8, e o ao vivo degradou aos 60 s, com 37 s de atraso). O pós-reunião da
  reunião 8 somou ~1.533 s para 900 s de áudio (~1,7×; o G2 pede até 1,5×, plano B 2×, D-041). Opções: janela menor
  na diarização ou baixar a prioridade do worker enquanto a lease vale; decidir com o dono antes.
- Privacidade no log (contra a D-053): o `attach_log` do `app.py`, quando falha, loga o caminho do arquivo de log, com a
  pasta do usuário, e a linha de abertura de todo log grava `"banco": <pasta>` (`ui/app.py:147`). Trocar pelo código
  do erro e pôr o `app.py` no teste de privacidade.
- F5.7 (R-04 do L3): num Windows sem `WDA_EXCLUDEFROMCAPTURE`, só mudam a dica e o menu da bandeja, e o ícone fica
  igual; proposta: um selo no ícone enquanto houver chamada pendente.
- Testes: a suíte já travou antes do primeiro teste (29 e 30/10: 14 min parada com 2,4 s de CPU, e a seguinte passou em
  2 min; suspeita: a consulta WMI do `platform.machine()`); se voltar, rodar com a saída num arquivo, não por `| tail`.
  `test_rag_ui.py::test_the_system_loopback_never_opens_with_the_player_playing` falhou uma vez com `-x` (0 em 15
  depois); se voltar, guardar a saída e ver a espera do player.
- F5.11 (D-078): desfazer a voz transportada de um reprocessamento não acha o vetor (a linha "Para desfazer" não
  aparece nela); o `--hf-check` atrás da inspeção TLS da empresa só o dono testa (Parte 22).
- O assistente atende o Enter com o "Sim" na pergunta da pasta sincronizada (`ui/wizard.py:121`, sem botão padrão,
  como o descarte antes da D-076): uma tecla põe o banco numa pasta sincronizada com a nuvem.
- Suíte acima da meta de 8 s (D-050; a inteira levou 152 s com 1.376 testes em 31/10): a meta pede execução paralela
  (pergunta 6); o resto é lógica espalhada (o `test_store` sozinho dá 5 s), não o EDR. Testes e sondas deixam pastas
  `listener-*` no `%TEMP%` (82 em três dias, apagadas na D-052): passar os testes para o `tmp_path` e as sondas a apagar
  o que não imprimem.
- Captura: a saúde dos canais começa "ok" por até 10 s sem sinal e o R não avisa antes (D-050: silêncio no começo é
  normal); rever depois do G1. A deriva de relógio só é instrumentada (A7); corrigir em spec própria se o G1 mostrar
  mais de 100 ms por hora (P22). O eco é medido no "Testar 3 s" e depois de trocar o microfone (F5.9), não de forma
  contínua; o `--check` dá o número preciso.
- F4: o eco do ao vivo usa o limiar do aviso (`capture.echo_corr_min`, 0,25); calibrar no G4 sem headset.
- F2: o worker pesado carrega o CTranslate2 antes do torch, a ordem inversa à do F0; se aparecer "OMP: Error #15" (dois
  `libiomp5md.dll`), importar o torch antes, como o ao vivo já faz (a reunião 8 processou sem ele).
- F2: invalidar um vetor de confirmação tira-o do centroide, mas a amostra já entrou na média e no desvio do limiar
  (Welford não é reversível sem guardar as amostras); aceito até o G2 mostrar impacto.
- F2: durante um reprocessamento a revisão mostra o job concluído (a abertura antecipada, P1, vale só para o primeiro
  processamento); `embed_range` (trecho livre) e separar perfis ficam para a F6.
- F2: dois workers leves podem nascer no mesmo segundo (D-056): o segundo não acha job e sai ocioso em 60 s; inofensivo
  (o claim é atômico); resolver com uma marca de lançamento no banco, se valer.
- Identidade no preset sala (R-06 da F5.5): a costura numera `SPK_n` por canal, e `R/SPK_0` e `L/SPK_0` chegam à
  identidade os dois como "SPK_0"; só um dos clusters recebe a sugestão, talvez o errado. Proposta: o rótulo com o canal
  na entrada da identidade e a busca por (canal, rótulo), numa spec pequena.
- F5: o filtro de pessoa varre `rag_chunks` (~35 ms a cada 20 mil trechos) e `vector_version` conta o índice a cada
  busca: medir com o banco real.
- Os preços da geração 3 do Gemini não estão nas sementes (D-055): a estimativa usa os do 2.5 [chute]; conferir com a
  fatura e cadastrar em Configurações › LLM › Gemini. No log, o "Windows fatal exception: code 0x8001010d" é o
  faulthandler registrando uma exceção COM já tratada: ruído, não queda (D-055).
- Código acima de 400 linhas, dividir ao mexer: `controller.py`, `live/proc.py`, `player.py`, `recorder.py`,
  `llm_steps.py` e outros; `rag/agent.py`, `ui/history_ask.py` e `rag/sandbox.py` perto do limite; o
  `MeetingWindow.__init__` com ~88 linhas efetivas.
- Interface: a 640 px o campo do título da reunião rola e esconde a primeira letra; a sonda às vezes acusa o índice do
  Histórico cortado no print `historico-abrindo` (o texto chega antes do layout do canto das abas; no print seguinte
  sai inteiro); os textos são fixos em pt-BR (prever i18n, `tr()` do Qt ou gettext, antes da F6; CP15).
- CP17: subir o piso e o teto do `requires-python` quando WhisperX e torch aceitarem Python 3.14 (hoje `<3.14`; o 3.13
  já está no CI). O lock é só do Windows (`requirements/windows.txt`); o CI e o Linux seguem com o `pyproject.toml` sem
  trava, e um lock do Linux pede o índice CPU do torch (o do PyPI traz CUDA, CP14).

## Medições a registrar em `decisoes.md`
pyannote por hora numa gravação de ~1 h (B5; a primeira medida, de 15 min, em 30/10) · tamanho do banco por hora (G1)
· deriva por canal e deslocamento L/R (G1, `--check`) · sonda Q8 e similaridade intra e inter-pessoa por plataforma
(F2) · precisão dos perfis, desvio do clique e legibilidade (G2) · embedding por janela no `live_probe` (B15); atraso
médio e p95, CPU e acerto do nome (G4) · `rag_probe gemini` por lote, varredura da primeira abertura, híbrida com o
vetor da consulta e `underflowed` do player com o worker pesado de verdade; citações certas em 10 perguntas (G5) · o G5
com o embedder local e a RAM com o Histórico aberto (F5.1, P23) · as 15 perguntas do G5.10.
