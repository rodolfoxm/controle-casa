**Correção:** o programa gastava a cota grátis de chamadas da Tuya (cerca de 26.000 por mês) em poucos dias e o ar parava com o erro 28841004.

- Escondido na bandeja, o programa não consulta mais a nuvem.
- Com o painel aberto, consulta no máximo uma vez por minuto, ou a cada 10 minutos se você não estiver usando.
- Depois de um comando, o painel atualiza na hora sem reler a nuvem.
- Os cômodos do Smart Life são lidos uma vez por dia, não mais a cada 10 minutos.
- Em Ajustes → Diagnóstico aparece quantas chamadas o programa já gastou no mês.
- Se a cota acabar, o programa para de insistir e mostra uma mensagem clara.

Numa simulação de uso normal, o consumo caiu de cerca de 170 mil para cerca de 3 mil chamadas por mês.

**Correção:** o painel ficava vazio quando a nuvem falhava.

- O programa guarda a última lista de aparelhos que deu certo (só nome, tipo e estado; sem chaves, IP ou localização) e mostra ela se a Tuya bloquear ou a Positivo não responder.
- A conexão com a nuvem da Positivo tenta de novo, até 3 vezes, quando demora para responder.
- Se a Positivo falhar, o painel não fica carregando: espera 1 minuto antes de tentar de novo.
