(() => {
  const token = (location.pathname.split("/").filter(Boolean).pop() || "");
  const board = document.getElementById("board");
  const subtitle = document.getElementById("subtitle");
  const sheet = document.getElementById("sheet");
  const toast = document.getElementById("toast");
  let state = null;
  let openId = null;
  let marked = new Set();
  let forfeitAsk = false;
  let socket = null;
  let toastTimer = 0;

  function artUrl(file) {
    if (!/^[a-z0-9-]+\.jpg$/.test(file || "")) return "";
    return "/art/" + file;
  }

  function showToast(text) {
    if (!text) return;
    toast.hidden = false;
    toast.textContent = text;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 3200);
  }

  async function send(path, body) {
    const response = await fetch("/api/" + token + "/" + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showToast(data.error || "That didn't land.");
      if (data.state) render(data.state);
      return;
    }
    if (data.notice) showToast(data.notice);
    render(data);
  }

  function render(next) {
    const previous = state && state.log && state.log.length ? state.log[state.log.length - 1] : null;
    state = next;
    const latest = state && state.log && state.log.length ? state.log[state.log.length - 1] : null;
    if (previous && latest && latest !== previous && !state.notice) showToast(latest);
    if (state.tombstone || (state.over && !state.you?.hand)) {
      renderEnded();
      return;
    }
    const you = state.you;
    const opp = state.opponent;
    const yourTurn = !!state.your_turn;
    subtitle.textContent = headline();
    const known = new Set((you.hand || []).map((card) => card.id));
    marked = new Set([...marked].filter((id) => known.has(id)));

    board.innerHTML = "";
    board.append(players(you, opp));
    board.append(pills());
    board.append(handBlock(you));
    board.append(actions());
    board.append(logBlock());
    if (state.pending && state.pending.type === "stash") {
      openStash(state.pending.cards || []);
    } else if (openId) {
      const card = (you.hand || []).find((item) => item.id === openId);
      if (card) openCard(card);
      else closeSheet();
    } else if (forfeitAsk) {
      openForfeit();
    } else {
      closeSheet();
    }
    if (state.over) openResult();
  }

  function headline() {
    if (!state) return "";
    if (state.over) return state.summary || "The duel is over.";
    const left = state.seconds_left;
    const clock = left == null ? "" : " · " + formatClock(left);
    if (state.your_turn) return "Round " + state.round + " · Your turn" + clock;
    return "Round " + state.round + " · " + (state.active_name || "Opponent") + "'s turn" + clock;
  }

  function formatClock(seconds) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return m + ":" + String(s).padStart(2, "0");
  }

  function players(you, opp) {
    const wrap = document.createElement("div");
    wrap.className = "players";
    wrap.append(panel(you, true), panel(opp, false));
    return wrap;
  }

  function panel(player, mine) {
    const el = document.createElement("section");
    el.className = "panel " + (mine ? "you" : "them");
    const who = document.createElement("div");
    who.className = "who";
    const name = document.createElement("h2");
    name.textContent = player.name || "Duelist";
    who.append(name);
    const meta = document.createElement("div");
    meta.className = "meta";
    const house = [player.house_emoji, player.house].filter(Boolean).join(" ");
    const extra = mine ? "You" : ((player.hand_count == null ? "" : player.hand_count + " cards"));
    meta.textContent = [house, extra].filter(Boolean).join(" · ");
    const ward = document.createElement("div");
    ward.className = "ward";
    const fill = document.createElement("span");
    const current = Number(player.ward || 0);
    const max = Number(player.ward_max || 20);
    fill.style.width = Math.max(0, Math.min(100, (current / max) * 100)) + "%";
    ward.append(fill);
    const label = document.createElement("div");
    label.className = "ward-label";
    label.textContent = "Ward " + current + " / " + max;
    const statuses = document.createElement("div");
    statuses.className = "statuses";
    statuses.textContent = (player.statuses || []).join(" · ");
    el.append(who, meta, ward, label, statuses);
    return el;
  }

  function pills() {
    const row = document.createElement("div");
    row.className = "pills";
    const you = state.you || {};
    const mana = document.createElement("div");
    mana.className = "pill" + (state.your_turn ? "" : " wait");
    mana.textContent = "Mana " + (you.mana ?? 0) + " / " + (you.mana_max ?? 0);
    const played = document.createElement("div");
    played.className = "pill";
    const who = state.your_turn ? "Cards played" : ((state.active_name || "They") + " played");
    played.textContent = who + " " + (state.spells_played ?? 0) + " / " + (state.spell_slots ?? 2);
    const spec = document.createElement("div");
    spec.className = "pill" + (state.specialist === "ready" ? " gold" : "");
    spec.textContent = specialistLabel();
    const hand = document.createElement("div");
    hand.className = "pill";
    hand.textContent = "Hand " + (you.hand_count ?? 0) + " / 6";
    row.append(mana, played, spec, hand);
    return row;
  }

  function specialistLabel() {
    if (state.specialist === "ready") return "★ Specialist: ready (every 3rd turn)";
    if (state.specialist === "spent") return "★ Specialist: spent this turn";
    return "★ Specialist: every 3rd turn";
  }

  function handBlock(you) {
    const wrap = document.createElement("section");
    wrap.className = "hand-wrap";
    const title = document.createElement("h3");
    const discarding = state.pending && state.pending.type === "discard";
    title.textContent = discarding
      ? "Choose " + state.pending.need + " to discard"
      : "Your hand (only you can see this)";
    const row = document.createElement("div");
    row.className = "hand";
    (you.hand || []).forEach((card) => row.append(cardEl(card)));
    const note = document.createElement("p");
    note.className = "rules-note";
    note.textContent = "Ward 20. Draw 2 a turn. Mana 3, then +1 up to 8. Two attack or defense cards a turn, and one specialist every 3rd turn of yours. End holding at most 6.";
    wrap.append(title, row, note);
    return wrap;
  }

  function cardEl(card) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "card school-" + (card.school || "star");
    if (marked.has(card.id)) button.classList.add("marked");
    if (!card.playable && !(state.pending && state.pending.type === "discard")) {
      button.classList.add("dim");
    }
    const art = document.createElement("div");
    art.className = "art";
    const url = artUrl(card.art);
    if (url) art.style.backgroundImage = "url('" + url + "')";
    const cost = document.createElement("div");
    cost.className = "cost" + (card.pay !== card.cost ? " surcharge" : "");
    cost.textContent = String(card.pay != null ? card.pay : card.cost);
    const body = document.createElement("div");
    body.className = "body";
    const name = document.createElement("strong");
    name.textContent = card.name;
    const tag = document.createElement("div");
    tag.className = "tagline";
    tag.textContent = card.school_name + " · " + card.tag;
    const rules = document.createElement("p");
    rules.className = "rules";
    rules.textContent = card.text;
    body.append(name, tag, rules);
    button.append(art, cost, body);
    button.addEventListener("click", () => {
      if (state.pending && state.pending.type === "discard") {
        if (marked.has(card.id)) marked.delete(card.id);
        else marked.add(card.id);
        openId = card.id;
        render(state);
        return;
      }
      openId = card.id;
      forfeitAsk = false;
      openCard(card);
    });
    return button;
  }

  function actions() {
    const row = document.createElement("div");
    row.className = "actions";
    const discardMode = state.pending && state.pending.type === "discard";
    const end = document.createElement("button");
    end.type = "button";
    end.className = "primary";
    if (discardMode) {
      end.textContent = "Discard " + marked.size + " / " + state.pending.need;
      end.disabled = marked.size !== state.pending.need || !state.your_turn;
      end.addEventListener("click", () => send("discard", { cards: [...marked] }));
    } else {
      end.textContent = "End turn";
      end.disabled = !state.your_turn || state.over || (state.pending && state.pending.type === "stash");
      end.addEventListener("click", () => send("end"));
    }
    const quit = document.createElement("button");
    quit.type = "button";
    quit.className = "danger";
    quit.textContent = "Forfeit";
    quit.disabled = !!state.over;
    quit.addEventListener("click", () => {
      forfeitAsk = true;
      openId = null;
      openForfeit();
    });
    row.append(quit, end);
    return row;
  }

  function logBlock() {
    const details = document.createElement("details");
    details.className = "log";
    const summary = document.createElement("summary");
    summary.textContent = "Duel log";
    const list = document.createElement("ol");
    (state.log || []).forEach((line) => {
      const item = document.createElement("li");
      item.textContent = line;
      list.append(item);
    });
    details.append(summary, list);
    return details;
  }

  function closeSheet() {
    sheet.hidden = true;
    sheet.innerHTML = "";
  }

  function openCard(card) {
    sheet.hidden = false;
    sheet.innerHTML = "";
    const popup = document.createElement("div");
    popup.className = "popup school-" + (card.school || "star");
    const hero = document.createElement("div");
    hero.className = "hero";
    const url = artUrl(card.art);
    if (url) hero.style.backgroundImage = "url('" + url + "')";
    const title = document.createElement("h2");
    title.textContent = card.name;
    const row = document.createElement("div");
    row.className = "pills";
    [card.school_name, "Cost " + card.cost, card.tag].forEach((label) => {
      const pill = document.createElement("div");
      pill.className = "pill";
      pill.textContent = label;
      row.append(pill);
    });
    const text = document.createElement("p");
    text.className = "text";
    text.textContent = card.text;
    const why = document.createElement("p");
    why.className = "why";
    const discardMode = state.pending && state.pending.type === "discard";
    if (discardMode) {
      why.textContent = marked.has(card.id) ? "Marked to discard." : "Not marked.";
    } else if (!card.playable) {
      why.textContent = card.why || "";
    }
    const buttons = document.createElement("div");
    buttons.className = "row";
    const go = document.createElement("button");
    go.type = "button";
    go.className = "gold";
    const back = document.createElement("button");
    back.type = "button";
    back.className = "ghost";
    back.textContent = "Back";
    back.addEventListener("click", () => {
      openId = null;
      closeSheet();
      if (discardMode) render(state);
    });
    if (discardMode) {
      go.textContent = marked.has(card.id) ? "Unmark" : "Mark to discard";
      go.addEventListener("click", () => {
        if (marked.has(card.id)) marked.delete(card.id);
        else marked.add(card.id);
        render(state);
      });
    } else {
      go.textContent = "Play";
      go.disabled = !card.playable;
      go.addEventListener("click", () => {
        openId = null;
        closeSheet();
        send("play", { card: card.id });
      });
    }
    buttons.append(go, back);
    popup.append(hero, title, row, text, why, buttons);
    sheet.append(popup);
    sheet.onclick = (event) => {
      if (event.target === sheet) {
        openId = null;
        closeSheet();
      }
    };
  }

  function openStash(cards) {
    sheet.hidden = false;
    sheet.innerHTML = "";
    const popup = document.createElement("div");
    popup.className = "popup stash";
    const title = document.createElement("h2");
    title.textContent = "Squirrel's Stash";
    const lead = document.createElement("p");
    lead.className = "text";
    lead.textContent = "Draw 2, keep the best. The other is discarded.";
    const pair = document.createElement("div");
    pair.className = "pair";
    cards.forEach((card) => {
      const col = document.createElement("div");
      const face = cardEl(card);
      face.disabled = true;
      const keep = document.createElement("button");
      keep.type = "button";
      keep.className = "gold";
      keep.textContent = "Keep " + card.name;
      keep.style.marginTop = "8px";
      keep.style.width = "100%";
      keep.addEventListener("click", () => {
        closeSheet();
        send("choose", { keep: card.id });
      });
      col.append(face, keep);
      pair.append(col);
    });
    popup.append(title, lead, pair);
    sheet.append(popup);
    sheet.onclick = null;
  }

  function openForfeit() {
    sheet.hidden = false;
    sheet.innerHTML = "";
    const popup = document.createElement("div");
    popup.className = "popup";
    const title = document.createElement("h2");
    title.textContent = "Forfeit this duel?";
    const text = document.createElement("p");
    text.className = "text";
    text.textContent = "Your opponent wins, and the table link closes.";
    const buttons = document.createElement("div");
    buttons.className = "row";
    const yes = document.createElement("button");
    yes.type = "button";
    yes.className = "danger";
    yes.textContent = "Forfeit";
    yes.addEventListener("click", () => {
      forfeitAsk = false;
      closeSheet();
      send("forfeit");
    });
    const no = document.createElement("button");
    no.type = "button";
    no.className = "ghost";
    no.textContent = "Back";
    no.addEventListener("click", () => {
      forfeitAsk = false;
      closeSheet();
    });
    buttons.append(yes, no);
    popup.append(title, text, buttons);
    sheet.append(popup);
  }

  function openResult() {
    if (sheet.querySelector(".end")) return;
    sheet.hidden = false;
    sheet.innerHTML = "";
    const popup = document.createElement("div");
    popup.className = "popup";
    const block = document.createElement("div");
    block.className = "end";
    const title = document.createElement("h2");
    title.textContent = state.you_won ? "You win" : "You lose";
    const text = document.createElement("p");
    text.className = "text";
    text.textContent = state.summary || "The duel is over. The result is in Discord.";
    block.append(title, text);
    popup.append(block);
    sheet.append(popup);
  }

  function renderEnded() {
    subtitle.textContent = state.summary || "The duel is over.";
    board.innerHTML = "";
    const popup = document.createElement("section");
    popup.className = "panel";
    const title = document.createElement("h2");
    title.textContent = state.you_won ? "You win" : "You lose";
    const text = document.createElement("p");
    text.textContent = state.summary || "This table link has closed. The result was posted in Discord.";
    const wards = document.createElement("p");
    wards.className = "meta";
    const you = state.you || {};
    const opp = state.opponent || {};
    if (you.ward != null && opp.ward != null) {
      wards.textContent = (you.name || "You") + " Ward " + you.ward + " · " + (opp.name || "Opponent") + " Ward " + opp.ward;
    }
    popup.append(title, text, wards);
    board.append(popup);
    closeSheet();
  }

  async function poll() {
    try {
      const response = await fetch("/api/" + token + "/state");
      if (response.status === 404) {
        subtitle.textContent = "This table link doesn't work.";
        return;
      }
      const data = await response.json();
      render(data.error ? (data.state || state) : data);
    } catch (_err) {
      /* the next poll or the socket will catch up */
    }
  }

  function connect() {
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(scheme + "//" + location.host + "/ws/" + token);
    socket.onmessage = (event) => {
      try { render(JSON.parse(event.data)); } catch (_err) { /* ignore malformed frames */ }
    };
    socket.onclose = () => {
      socket = null;
      setTimeout(connect, 1500);
    };
  }

  setInterval(() => {
    if (!socket || socket.readyState !== 1) poll();
    else if (state && state.seconds_left != null && state.your_turn && !state.over) {
      state.seconds_left = Math.max(0, state.seconds_left - 1);
      subtitle.textContent = headline();
    }
  }, 1000);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      openId = null;
      forfeitAsk = false;
      closeSheet();
    }
  });

  poll();
  connect();
})();
