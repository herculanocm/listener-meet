# Roadmap — o que vem pela frente

Quando ler: ao planejar a próxima entrega. O que já foi entregue está no `CHANGELOG.md`, e o contrato de cada feature,
em `features/`. O estado de cada item está no `backlog.md`. Esforço em dias de foco é [chute]. Tamanho-alvo: 3 KB.

## Entregue
F1 a F5.21 estão implementadas (versão 0.14.0). Até a F5.15, os gates foram dados por cumpridos pelo uso real do app
no Windows, sem os números (D-090). Da F5.16 em diante, o aceite de cada spec é conferido no uso, e o que falta está em
"Verificar no Windows", no backlog.

## Próximas entregas
| Fase | Entrega | Gate, medido em uso real | Esforço |
| --- | --- | --- | --- |
| F5.22 GPU quando houver | o adaptador detectado; `pipeline.device` e `live.device` (auto, CPU, GPU) com queda para CPU; o lock `windows-gpu.txt` só no venv; o `.exe` segue só CPU. Pede a emenda da CP14 e a decisão do dono (P24) | sem CUDA, nenhuma regressão; numa NVIDIA, pós-reunião em até 0,25× a duração e ao vivo com p95 de até 6 s [chute] | 3 a 4 d |
| F6 Telas completas | janela principal (Gravar, Histórico, Perfis, Configurações); tela de Perfis (criar de um trecho, mesclar, separar, vozes desconhecidas); todo o CP8 editável em uso; presets; tamanho do banco e backup; mover o banco; tema escolhido à mão | G6: uma semana de uso diário sem terminal; toda configuração do CP8 editável durante o uso | 7 a 10 d |
| F7 Linux e macOS | adaptadores em `listener/platform/linux/` e `macos/` e o empacotamento por sistema (CP15) | G7: os testes da plataforma simulada passam nos três sistemas; uma reunião real em cada | sem estimativa |

## Ideias avaliadas, sem spec
- Refazer a sugestão das vozes pendentes também nas reuniões antigas quando uma pessoa ganha uma referência nova, e
  apontar a referência de voz que parece confirmação errada, sem apagar nada. Só se o uso mostrar gente conhecida
  caindo em pendente ou com o nome errado.
- Tradução da interface: os textos são fixos em pt-BR; prever `tr()` do Qt ou gettext antes da F6 (CP15).
- Correção da deriva de relógio entre os canais: hoje só é medida; vira spec se uma reunião mostrar mais de 100 ms por
  hora no `--check` (P22).
