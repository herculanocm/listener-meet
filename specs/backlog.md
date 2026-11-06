# Backlog — estado do projeto e o que fazer agora

Quando ler: sempre (T0). Único lugar de estado; atualize ao fim de cada sessão. Tamanho-alvo: 5 KB.

## Onde paramos (06/11/2026, Windows nativo; tudo commitado; o push é do dono)
- Código: F1 a F5.17 implementadas (o detalhe na seção Implementação de cada spec; as decisões em `decisoes.md`); até a
  F5.15, verificadas pelo uso real do app no Windows, sem os números dos gates (D-090). F6 é rascunho e fica para
  depois (D-091); F7 é futuro. Esquema v9, versão 0.10.0 (D-081).
- **F5.16 implementada** (05/11, D-091, D-093, `features/F5.16-sem-token-hugging-face.md`): sem o token do Hugging
  Face, a reunião sai transcrita com o R "Desconhecido" e o aviso do U-139; reprocessar sem o token não tira vozes já
  identificadas. Falta o dono: usar uma reunião sem o token (ou só seguir usando; nada muda com ele).
- **F5.17 implementada** (06/11, D-094, D-095, `features/F5.17-bandeja-de-legendas.md`): a legenda ao vivo numa
  bandeja própria, como chat, fora do widget, com minimizar e fechar; a janela Ao vivo ganhou a mesma lista (uma
  legenda nova de 73 para 2 ms com 2.000 falas). Falta o dono: usar numa reunião (Aceite 4, em "Verificar no
  Windows").
- O `.exe` atual segue o 0.9.0 de 05/11 (`13b8d37`, a F5.16; 524 s, 892 MB, 4.697 arquivos; sonda `--probe` ok); o
  0.10.0 ainda não foi gerado. Para compartilhar, o `build\exe\listener-meet-0.9.0-win64.zip` (351 MB, a pasta
  inteira; D-081: `build_exe.py --zip`).
- Sem CI: o `ci.yml` saiu do repositório (D-090); os testes rodam na máquina, antes de cada commit, em paralelo (D-091).

## Como retomar (5 minutos)
1. T0: `AGENTS.md` (primeiro: em que ambiente a sessão está), `specs/01-clausulas.md` e este arquivo; depois só a
   camada da tarefa.
2. Testes (D-050): `python -m pytest -q` é a parte rápida; antes de commitar, a inteira em paralelo (`--tudo -n 4
   --dist loadgroup`, D-091), mais `ruff check .`, `mypy` e `mypy --platform linux`. No Windows, pelo venv do checkout:
   `.venv\Scripts\python.exe -m pytest -q --tudo -n 4 --dist loadgroup`, `.venv\Scripts\ruff.exe check .` e
   `.venv\Scripts\mypy.exe`.
3. Antes de código: a spec da entrega em `specs/features/` está `aprovada`? (CP13)

## Verificar no Windows (quando a sessão estiver no Windows nativo)
- F5.17 (Aceite 4, no uso): a bandeja aberta, recolhida, fechada e reaberta numa reunião; a roda do mouse, as dicas e
  o clique direito (o menu fecha com o clique fora e não fecha sozinho); o Teams compartilhando a tela sem a bandeja;
  dois monitores com escalas diferentes.

Antes disso, nada pendente: em 04/11 o dono considerou feitas todas as verificações (as Partes 1 a 26 de
`specs/roteiro-teste-windows.md`) pelo uso real do app (D-090). Entrega nova feita fora do Windows põe aqui o que só o
Windows verifica (`AGENTS.md`); o dono confere no uso, e o roteiro fica como referência. Sobra da Parte 3, com o OK
dele: apagar o venv do F0 e as duas entradas vazias de 24/10 no cache do Hugging Face (D-050, D-052).

## Perguntas abertas ao dono (padrão entre parênteses; só vale com a resposta dele)
Nenhuma: as últimas (1, 5 a 10, 17 e 19) foram fechadas no padrão em 04/11 (D-091), e as anteriores estão em
`decisoes.md`, nas specs e no git.

## Bloqueios que só o dono destrava
Nenhum: B1, B2, B5 e B15 fecharam pelo uso real, sem os números que pediam (D-090); B3, B4 e B6 a B16 já estavam
fechados (as decisões em `decisoes.md`).

## Dívidas técnicas
- D-072, reais e pequenas (sem correção por ora): a morte da thread de trabalho da captura não marca a reunião; o
  "banco não responde há N s" exagera (`pending_since`); corridas de leitura e escrita fora da transação (sugestão da
  LLM num cluster recém-confirmado, ação superada que volta, `upsert_me`); o chat em streaming sem retentativa antes do
  primeiro pedaço e o cancelado gravado como `OK` sem custo; o preparo do ao vivo sem prazo no `repo_info` e sem
  Cancelar; o `db-location.json` sem gravação atômica.
- F5.10 (D-075): o `--downgrade` com o app aberto faz o chat falhar até reabrir (nada se perde); a `memory_rss` do
  Windows mede o working set, que o sistema pode encolher, e o teto da consulta mediria menos que o real.
- Instável sob carga, fora da F5.14: `test_call_watch.py::test_the_app_exits_with_code_0_with_the_sensor_stuck_in_the_audio_system`
  falhou 1 vez na suíte do L2 da F5.14 (faltou "não parou em 1000 ms" no log) e passou nas 3 rodadas sozinho e na suíte
  seguinte.
- F5.9, das revisões: `test_call_watch.py::test_failures_degrade_with_one_warning_and_recover_with_one_info` é instável
  na suíte inteira (3 falhas em 7; 9/9 isolado; de novo em 06/11, 3/3 isolado); o `pyproject` não liga o `C901`
  (o limite de complexidade 10 é só convenção); o nativo do proctap cai no sistema inteiro calado em caminhos de
  falha (`is_process_specific()` diz quando) e somaria o sistema inteiro no canal do app sem aviso; o pedido de saída
  ambíguo (nome repetido no loopback) não fica no handle, e a vigia pode pedir `back` de novo a cada mudança de
  assinatura.
- F2 (Q7), a lease lida só entre janelas: a janela de diarização de 1.200 s (RTF 1,2 a 1,6) atravessa uma gravação
  inteira (o job 47 diarizou durante a reunião 8, e o ao vivo degradou aos 60 s, com 37 s de atraso). O pós-reunião da
  reunião 8 somou ~1.533 s para 900 s de áudio (~1,7×; o G2 pede até 1,5×, plano B 2×, D-041). Opções: janela menor
  na diarização ou baixar a prioridade do worker enquanto a lease vale; decidir com o dono antes.
  Em 06/11, de novo na reunião 22: o job 135, da 21, seguia em andamento na gravação inteira (p95 de 25 s, máximo de
  92 s; degradou e pulou janelas). Pela D-094, é esta a alavanca do atraso do ao vivo, e não o paralelismo.
- F5.7 (R-04 do L3): num Windows sem `WDA_EXCLUDEFROMCAPTURE`, só mudam a dica e o menu da bandeja, e o ícone fica
  igual; proposta: um selo no ícone enquanto houver chamada pendente.
- Testes: a suíte já travou antes do primeiro teste (29 e 30/10: 14 min parada com 2,4 s de CPU, e a seguinte passou em
  2 min; suspeita: a consulta WMI do `platform.machine()`); se voltar, rodar com a saída num arquivo, não por `| tail`.
  `test_rag_ui.py::test_the_system_loopback_never_opens_with_the_player_playing` falhou uma vez com `-x` (0 em 15
  depois); se voltar, guardar a saída e ver a espera do player.
- F5.11 (D-078): desfazer a voz transportada de um reprocessamento não acha o vetor (a linha "Para desfazer" não
  aparece nela).
- Suíte acima da meta de 8 s (D-050): com o `pytest-xdist` (D-091), a inteira caiu de 287 para 139 s com `-n 4
  --dist loadgroup` (os arquivos que lançam subprocesso num processo só, `SUBPROCESS_FILES` no `conftest`) e a rápida
  de 81 para 62 s; mais processos pioram nesta máquina (2 núcleos de desempenho). O resto é lógica espalhada (o
  `test_store` sozinho dá 5 s), não o EDR. Testes e sondas deixam pastas
  `listener-*` no `%TEMP%` (82 em três dias, apagadas na D-052): passar os testes para o `tmp_path` e as sondas a apagar
  o que não imprimem.
- Captura: a saúde dos canais começa "ok" por até 10 s sem sinal e o R não avisa antes (D-050: silêncio no começo é
  normal); rever se aparecer no uso. A deriva de relógio só é instrumentada (A7); corrigir em spec própria se o
  `--check` de uma reunião mostrar mais de 100 ms por hora (P22). O eco é medido no "Testar 3 s" e depois de trocar o microfone (F5.9), não de forma
  contínua; o `--check` dá o número preciso.
- F4: o eco do ao vivo usa o limiar do aviso (`capture.echo_corr_min`, 0,25); calibrar se o eco vazar sem headset.
- F2: o worker pesado carrega o CTranslate2 antes do torch, a ordem inversa à do F0; se aparecer "OMP: Error #15" (dois
  `libiomp5md.dll`), importar o torch antes, como o ao vivo já faz (a reunião 8 processou sem ele).
- F5.15: quem junta 5 notas de grupo passa a exigir ~0,75 (as notas ficam acima de 0,8 pelo corte; A-01); o L0 não
  viu, porque ninguém tinha 5. Acompanhar no uso; se a pessoa conhecida começar a cair em pendente na condição
  de sempre, a variante C (só o global) está a uma constante.
- Para depois (04/11, ideia do dono avaliada): se o uso mostrar gente conhecida caindo em pendente ou
  com o nome errado, uma spec pequena, local e por evento: (1) a referência nova refaz a sugestão das vozes pendentes
  também das reuniões antigas (hoje só na reunião aberta; nas outras, o Reprocessar); (2) uma checagem dos perfis aponta
  a referência que parece confirmação errada (amostra sozinha, longe das outras da pessoa e perto de outra), sem apagar
  nada (CP3). Uma rotina periódica com a LLM ficou de fora: a LLM não melhora a biometria, a CP11 a impede de nomear,
  e um palpite que virasse referência daria o nome errado sozinho.
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
- CP17: subir o piso e o teto do `requires-python` quando WhisperX e torch aceitarem Python 3.14 (hoje `<3.14`). O lock
  é só do Windows (`requirements/windows.txt`); o Linux segue com o `pyproject.toml` sem trava, e um lock do Linux pede o índice CPU do torch (o do PyPI traz CUDA, CP14).
