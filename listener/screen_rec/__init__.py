"""F5.27: a gravação da tela da reunião, sem Qt e sem torch (o PyAV só é importado dentro das funções que codificam).

- `codec`: os codificadores de CPU (`CODECS`) e a escolha do primeiro que abre (`pick_codec`), também usados pelo vídeo
  exportado (F5.19).
- `segment`: o pedaço de tela (`Segment`, um MP4 autocontido em memória), o codificador de um pedaço (`SegmentEncoder`), a
  conta do tamanho (`fit_size`) e a leitura de volta (`decode_segment`).
- `recorder`: o laço da gravação (`ScreenRecorder`), independente de processo: o grab, o relógio e as saídas chegam por
  parâmetro."""
