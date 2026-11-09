// ---------- Propostas: boletins só com escolhas «com valor» ----------
// Método (testado com 10 980 jogos reais de 2021/22 a 2026/27):
//   probabilidade real ≈ odds do mercado de referência (Pinnacle/Betfair) sem margem;
//   só se aposta quando a odd do Placard paga pelo menos +3% acima da odd justa.
// Usa as funções globais da página principal (guardar, ler, esc, num, horaLocal, toast,
// selecao, contarSelecoes, abrir, NOME_ESCOLHA, rotulo, euros).
(function () {
  const VALOR_MIN = 0.03, ODD_MIN = 1.5, ODD_MAX = 3.8, MIN_JOGOS = 3, MAX_JOGOS = 6;
  const MERCADOS = ['1', 'X', '2', '1X', 'X2', '+2.5', '-2.5'];
  const FONTE_PROP = 'Propostas (valor)';
  let placard = ler('placard_v1', {});           // "idEvento|mercado" -> odd escrita pelo utilizador
  let resposta = null;

  const f2 = x => Number(x).toFixed(2).replace('.', ',');
  const pc = p => Math.round(p * 100) + '%';
  const nomeMercado = (m, ev) => m === '1' ? `${ev.casa} vence` : m === '2' ? `${ev.fora} vence` : NOME_ESCOLHA[m];
  const chave = (ev, m) => ev.id + '|' + m;
  const minima = p => (1 + VALOR_MIN) / p;
  const diaSemana = d => new Date(d + 'T12:00:00Z').toLocaleDateString('pt-PT', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' });

  // Escolhas possíveis: odd justa dentro da faixa (para que a odd do Placard também caiba em 1,5–3,8)
  function candidatas(ev) {
    return MERCADOS.filter(m => ev.mercados[m]).map(m => ({ m, ...ev.mercados[m] }))
      .filter(x => x.odd_justa >= 1.40 && x.odd_justa <= ODD_MAX / (1 + VALOR_MIN));
  }

  function escolhasComValor(eventos) {
    const out = [];
    for (const ev of eventos) for (const c of candidatas(ev)) {
      const odd = num(placard[chave(ev, c.m)] ?? '');
      if (odd >= ODD_MIN && odd <= ODD_MAX && c.prob * odd - 1 >= VALOR_MIN)
        out.push({ ev, m: c.m, prob: c.prob, odd, ve: c.prob * odd - 1 });
    }
    // uma escolha por jogo: a de maior valor esperado
    const porJogo = {};
    for (const e of out) if (!porJogo[e.ev.id] || e.ve > porJogo[e.ev.id].ve) porJogo[e.ev.id] = e;
    return Object.values(porJogo).sort((a, b) => b.ve - a.ve);
  }

  function montarBoletins(lista) {
    if (lista.length < MIN_JOGOS) return [];
    const k = Math.max(1, Math.floor(lista.length / MIN_JOGOS));
    const grupos = Array.from({ length: k }, (_, i) => lista.filter((_, j) => j % k === i).slice(0, MAX_JOGOS));
    return grupos.filter(g => g.length >= MIN_JOGOS).map(g => {
      const odd = g.reduce((p, e) => p * e.odd, 1), prob = g.reduce((p, e) => p * e.prob, 1);
      return { g, odd, prob, ve: prob * odd - 1 };
    });
  }

  function usarBoletim(b) {
    selecao = {};
    for (const e of b.g) {
      const id = 'odds:' + e.ev.id;
      selecao[id] = { id, data: e.ev.data_local, casa: e.ev.casa, fora: e.ev.fora, liga: e.ev.liga,
        inicio: e.ev.inicio_utc, escolha: e.m, odd: String(e.odd).replace('.', ','), fonte: FONTE_PROP, prob: e.prob };
    }
    guardar('selecao_v1', selecao);
    valorBoletim = '1'; guardar('valor_v1', '1');
    contarSelecoes(); abrir('boletim');
    toast('Boletim preparado com 1 € — aposta no Placard e depois «Guardar boletim»');
  }

  function desenhar() {
    const el = document.getElementById('propostas');
    if (!resposta) { el.innerHTML = '<div class="vazio">A carregar…</div>'; return; }
    const est = resposta.estado, evs = resposta.eventos;
    const cabec = `<div class="cartao"><b>Como funciona</b>
      <p class="nota" style="margin-top:6px">1) A app mostra, para cada jogo, a <b>odd mínima</b> que o Placard tem de pagar
      para a aposta valer a pena (probabilidade real do mercado mais preciso + 3%).<br>
      2) Abre o Placard e escreve a odd que lá aparece. ✅ = compensa.<br>
      3) A app junta as escolhas ✅ em boletins de ${MIN_JOGOS} a ${MAX_JOGOS} jogos, com 1 € cada.</p>
      <p class="nota">Nos dados de 2021–2026, este método deu lucro a longo prazo, mas os boletins acertam poucas vezes
      (≈5% num boletim de 3 jogos com odds altas): são normais muitas semanas seguidas a perder.
      Nunca apostes dinheiro de que precises.</p>
      <p class="nota">Fonte: ${esc(est.fonte)}${est.ultima_atualizacao ? ` · atualizado ${new Date(est.ultima_atualizacao).toLocaleString('pt-PT', { timeZone: 'Europe/Lisbon', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}` : ''}${est.creditos_restantes != null ? ` · ${est.creditos_restantes} pedidos restantes este mês` : ''}</p></div>`;
    if (!est.chave_configurada) {
      el.innerHTML = cabec + `<div class="cartao aviso">Falta a chave da The Odds API no Render (linha <b>ODDS_API_KEY</b>).
        Sem ela não há probabilidades de referência.</div>`;
      return;
    }
    if (!evs.length) {
      el.innerHTML = cabec + `<div class="vazio">Sem jogos com odds de referência nas próximas 72 horas.<br>
        <small>As odds são atualizadas uma vez por dia, para as ligas com jogos nas 48 horas seguintes.</small></div>`;
      return;
    }
    const valor = escolhasComValor(evs);
    const bols = montarBoletins(valor);
    let html = cabec;
    html += `<h2 class="liga">Boletins propostos</h2>`;
    if (bols.length) {
      html += bols.map((b, i) => `<div class="cartao"><b>Boletim ${i + 1}</b> · ${b.g.length} jogos · odd ${f2(b.odd)}
        <div class="nota">Probabilidade real de acertar: <b>${(b.prob * 100).toFixed(1).replace('.', ',')}%</b> ·
          retorno com 1 €: ${euros(b.odd)} · valor esperado ${b.ve >= 0 ? '+' : ''}${Math.round(b.ve * 100)}%</div>
        ${b.g.map(e => `<div class="sel-linha" style="grid-template-columns:1fr auto"><div class="sel-nome"><b>${esc(e.ev.casa)} – ${esc(e.ev.fora)}</b>
          ${esc(nomeMercado(e.m, e.ev))} · ${horaLocal(e.ev.inicio_utc)}</div><div>${f2(e.odd)}</div></div>`).join('')}
        <button class="botao" data-usar="${i}">Usar este boletim (1 €)</button></div>`).join('');
    } else {
      html += `<div class="cartao nota">Ainda não há escolhas ✅ suficientes para um boletim de ${MIN_JOGOS} jogos
        (tens ${valor.length}). Escreve mais odds do Placard abaixo. É normal haver poucas: o Placard raramente paga
        acima do justo — é precisamente isso que torna estas escolhas valiosas.</div>`;
    }
    html += `<h2 class="liga">Odds mínimas a procurar no Placard</h2>`;
    let dia = null;
    for (const ev of evs) {
      const cs = candidatas(ev);
      if (!cs.length) continue;
      if (ev.data_local !== dia) { dia = ev.data_local; html += `<div class="dia-titulo">${esc(diaSemana(dia))}</div>`; }
      html += `<div class="jogo"><div class="equipas">${esc(ev.casa)} – ${esc(ev.fora)}</div>
        <div class="info">${esc(resposta.ligas[ev.liga] || ev.liga)} · ${horaLocal(ev.inicio_utc)}</div>
        ${cs.map(c => {
          const k = chave(ev, c.m), v = placard[k] ?? '', odd = num(v);
          const tem = odd >= ODD_MIN && odd <= ODD_MAX && c.prob * odd - 1 >= VALOR_MIN;
          const est = !(odd >= 1.01) ? '' : tem ? `<span class="vl-bom">✅ +${Math.round((c.prob * odd - 1) * 100)}%</span>`
            : `<span class="vl-mau">não compensa</span>`;
          return `<div class="sel-linha" style="grid-template-columns:1fr 70px auto;align-items:center">
            <div class="sel-nome"><b>${esc(nomeMercado(c.m, ev))}</b>
              prob. ${pc(c.prob)} · justa ${f2(c.odd_justa)} · <b>precisa ≥ ${f2(minima(c.prob))}</b></div>
            <input class="num" inputmode="decimal" placeholder="odd" value="${esc(v)}" data-pl="${esc(k)}" aria-label="Odd do Placard">
            <div data-est="${esc(k)}" style="font-size:.75rem;min-width:70px">${est}</div></div>`;
        }).join('')}</div>`;
    }
    el.innerHTML = html;
    el.querySelectorAll('[data-usar]').forEach(b => b.onclick = () => usarBoletim(bols[+b.dataset.usar]));
    el.querySelectorAll('[data-pl]').forEach(i => i.onchange = () => {
      const v = i.value.trim(); if (v) placard[i.dataset.pl] = v; else delete placard[i.dataset.pl];
      guardar('placard_v1', placard); desenhar();
    });
  }

  async function carregarPropostas() {
    try {
      const r = await fetch('/api/propostas', { cache: 'no-store' });
      resposta = await r.json();
      // limpar odds escritas para jogos que já passaram
      const ids = new Set(resposta.eventos.map(e => e.id));
      for (const k of Object.keys(placard)) if (!ids.has(k.split('|')[0])) delete placard[k];
      guardar('placard_v1', placard);
    } catch (e) {
      resposta = { estado: { chave_configurada: true, fonte: '—' }, eventos: [], ligas: {} };
    }
    desenhar();
  }

  const estilo = document.createElement('style');
  estilo.textContent = '.dia-titulo{margin:16px 2px 6px;font-weight:700;color:var(--suave);font-size:.85rem}.dia-titulo::first-letter{text-transform:uppercase}';
  document.head.appendChild(estilo);
  window.carregarPropostas = carregarPropostas;
  document.querySelector('nav button[data-sep="propostas"]')?.addEventListener('click', carregarPropostas);
  carregarPropostas();
})();
