// Altere estes exemplos quando os prêmios reais forem definidos.
const demoPrizes = [
  { name: 'Tesouro dourado', icon: '✦' },
  { name: 'Surpresa especial', icon: '✧' },
  { name: 'Relíquia da ilha', icon: '◇' },
];
const board = document.getElementById('gameBoard');
const revealBtn = document.getElementById('revealBtn');
const resetBtn = document.getElementById('resetBtn');
const resultText = document.getElementById('resultText');
const prizeList = document.getElementById('prizeList');
const selected = new Set();
const timers = new Set();
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let phase = 'select';
let revealed = 0;
const openImage = new Image();
openImage.src = 'bau%20aberto.gif';

function later(callback, delay) {
  const timer = setTimeout(() => { timers.delete(timer); callback(); }, delay);
  timers.add(timer);
}
function update() {
  document.getElementById('selectionCount').textContent = `${selected.size} de 3 selecionados`;
  document.querySelectorAll('.selection-dots span').forEach((dot, i) => dot.classList.toggle('filled', i < selected.size));
  [...board.children].forEach((button, i) => {
    const chosen = selected.has(i);
    button.classList.toggle('selected', chosen);
    button.classList.toggle('locked', !chosen && (selected.size === 3 || phase !== 'select'));
    button.disabled = phase !== 'select' || (!chosen && selected.size === 3);
    button.setAttribute('aria-pressed', String(chosen));
    button.querySelector('.check').textContent = chosen ? '✓' : '';
    if (phase === 'select') button.querySelector('.chest-tag').textContent = chosen ? 'Selecionado' : 'Escolher baú';
  });
  revealBtn.disabled = selected.size !== 3 || phase !== 'select';
  if (phase === 'select') {
    const remaining = 3 - selected.size;
    revealBtn.textContent = remaining ? `Escolha mais ${remaining} ${remaining === 1 ? 'baú' : 'baús'}` : 'Revelar meus tesouros  ↗';
    resultText.textContent = remaining ? `Escolha ${remaining === 3 ? '' : 'mais '}${remaining} ${remaining === 1 ? 'baú' : 'baús'} para começar a descoberta.` : 'Tudo pronto! Revele seus três tesouros.';
  }
}
function reset() {
  timers.forEach(clearTimeout);
  timers.clear();
  selected.clear();
  phase = 'select';
  revealed = 0;
  document.getElementById('confetti').replaceChildren();
  document.getElementById('boardTitle').textContent = 'Qual deles chama por você?';
  document.getElementById('resultTitle').textContent = 'Um lugar para seus tesouros';
  document.getElementById('rewardCount').textContent = '0 / 3 revelados';
  document.getElementById('hint').textContent = 'Você pode trocar suas escolhas antes de abrir.';
  board.replaceChildren();
  for (let i = 0; i < 8; i++) {
    const button = document.createElement('button');
    button.className = 'chest';
    button.type = 'button';
    button.setAttribute('aria-label', `Baú ${i + 1}`);
    button.innerHTML = `<span class="chest-number">0${i + 1}</span><span class="check" aria-hidden="true"></span><img src="bau%20fechado.gif" alt="" draggable="false"><span class="chest-tag">Escolher baú</span>`;
    button.addEventListener('click', () => {
      if (phase !== 'select') return;
      if (selected.has(i)) selected.delete(i);
      else if (selected.size < 3) selected.add(i);
      update();
    });
    board.append(button);
  }
  prizeList.replaceChildren();
  for (let i = 0; i < 3; i++) {
    const slot = document.createElement('div');
    slot.className = 'prize';
    slot.innerHTML = `<span class="prize-icon" aria-hidden="true">◇</span><div><strong>Tesouro ${String(i + 1).padStart(2, '0')}</strong><small>Aguardando sua descoberta</small></div>`;
    prizeList.append(slot);
  }
  update();
}
function celebrate() {
  if (reducedMotion) return;
  const container = document.getElementById('confetti');
  for (let i = 0; i < 55; i++) {
    const piece = document.createElement('span');
    piece.className = 'confetto';
    piece.style.left = `${Math.random() * 100}%`;
    piece.style.animationDelay = `${Math.random() * .7}s`;
    piece.style.background = ['#edc575', '#f4f0df', '#6ea990'][i % 3];
    container.append(piece);
  }
  later(() => container.replaceChildren(), 3800);
}
function reveal() {
  if (phase !== 'select' || selected.size !== 3) return;
  phase = 'opening';
  update();
  revealBtn.textContent = 'Revelando seus tesouros…';
  document.getElementById('boardTitle').textContent = 'A aventura está só começando…';
  document.getElementById('hint').textContent = 'Um pouquinho de suspense para cada descoberta.';
  [...selected].forEach((index, position) => {
    const button = board.children[index];
    later(() => {
      button.classList.add('opening');
      button.querySelector('.chest-tag').textContent = 'Abrindo…';
      later(() => {
        button.classList.remove('opening');
        button.classList.add('revealed');
        button.querySelector('img').src = 'bau%20aberto.gif';
        button.querySelector('.chest-tag').textContent = 'Tesouro encontrado';
        const prize = demoPrizes[position];
        button.setAttribute('aria-label', `Baú ${index + 1}: ${prize.name}`);
        const slot = prizeList.children[position];
        slot.classList.add('won');
        slot.querySelector('.prize-icon').textContent = prize.icon;
        slot.querySelector('strong').textContent = prize.name;
        slot.querySelector('small').textContent = `Baú ${index + 1} · Prêmio ilustrativo`;
        revealed++;
        document.getElementById('rewardCount').textContent = `${revealed} / 3 revelados`;
        resultText.textContent = `Baú ${index + 1}: ${prize.name}.`;
        if (revealed === 3) {
          phase = 'done';
          revealBtn.textContent = 'Todos os tesouros revelados  ✓';
          document.getElementById('boardTitle').textContent = 'Sua intuição encontrou tesouros!';
          document.getElementById('resultTitle').textContent = 'Três escolhas. Três tesouros.';
          document.getElementById('hint').textContent = 'Os três baús escolhidos guardavam uma surpresa.';
          resultText.textContent = 'Você descobriu os 3 prêmios! Jogue novamente para uma nova aventura.';
          celebrate();
        }
      }, reducedMotion ? 30 : 750);
    }, reducedMotion ? position * 80 : position * 1050 + 150);
  });
}
revealBtn.addEventListener('click', reveal);
resetBtn.addEventListener('click', reset);
reset();
