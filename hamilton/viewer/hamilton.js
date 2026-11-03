(() => {
  const LATENCY = 0.8;
  const WASTE = [781, 202.5, 150, 220];
  const SPEEDS = [1, 5, 20, 100];
  const FAILURES = 5;
  const OPS = {
    C0TT: "Set tip type", C0TP: "Pick up tips", C0TR: "Eject tips", C0AS: "Aspirate",
    C0DS: "Dispense", C0PP: "Get plate", C0PR: "Put plate", C0PG: "Park iSWAP",
    C0JM: "Move channels", C0KX: "Move arm", C0JY: "Move channels in Y", C0JZ: "Move channels in Z",
    C0RF: "Read firmware", C0RX: "Read arm X", C0RY: "Read channel Y", C0RZ: "Read channel Z",
    C0RT: "Read tips", C0QP: "Read gripper",
  };
  const ISWAP = new Set(["C0PP", "C0PR"]);
  const VERBS = { aspirate: "Aspirate", dispense: "Dispense", mix: "Mix", tip_pickup: "Pick up", tip_return: "Return" };
  const C = { ink: "#19201c", empty: "#e4e4e4", room: "#c8c8c8", hair: "#ced5cf", faint: "#6c756f", accent: "#2d593e", bad: "#c62828" };

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (key in node) node[key] = value;
      else node.setAttribute(key, value);
    }
    node.append(...children);
    return node;
  }

  function svg(parent, tag, attrs) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
    parent.append(node);
    return node;
  }

  function fields(wire) {
    const out = {};
    const tail = wire.replace(/^C0[A-Z]{2}(id\d{4})?/, "");
    for (const [, name, values] of tail.matchAll(/([a-z]{2})(-?\d+(?: -?\d+)*)/g)) {
      out[name] = values.split(" ").map(Number);
    }
    return out;
  }

  function normalize(raw) {
    const command = { ...raw, events: raw.events || [], rested: raw.rested || [] };
    const f = command.wire ? fields(command.wire) : {};
    const mm = (values) => values && values.map((v) => v / 10);
    const collided = command.events.some((e) => e.kind === "collision");
    const failed = "ok" in command ? !command.ok : command.refused || command.error || !/er00/.test(command.reply);
    return {
      command,
      op: command.op || command.wire.slice(0, 4),
      x: command.x ?? mm(f.xp || f.xs)?.[0],
      ys: command.ys ?? mm(f.yp),
      tm: f.tm && Array.from({ length: 8 }, (_, i) => f.tm[Math.min(i, f.tm.length - 1)]),
      failed: failed || collided,
    };
  }

  function shown(ys, i) {
    return ys[i] > 0 && (i === 0 || ys[i] < ys[i - 1]);
  }

  function spots(rack) {
    const pitch = rack.tip_pitch_mm;
    const [ox, oy] = rack.tip_origin_mm;
    const cols = Math.round((rack.size_mm[0] - 2 * ox) / pitch) + 1;
    const rows = Math.round((2 * oy - rack.size_mm[1]) / pitch) + 1;
    const out = [];
    for (let col = 0; col < cols; col++) {
      for (let row = 0; row < rows; row++) {
        out.push({ name: String.fromCharCode(65 + row) + (col + 1), x: ox + col * pitch, y: oy - row * pitch });
      }
    }
    return out;
  }

  function moduleBox(module, nests) {
    const nest = nests[module.nest];
    const bodies = module.geometry?.bodies || [];
    if (!nest || !bodies.length) return null;
    const lo = [0, 1].map((k) => nest.origin_mm[k] + Math.min(...bodies.map((b) => b.min[k])));
    const hi = [0, 1].map((k) => nest.origin_mm[k] + Math.max(...bodies.map((b) => b.max[k])));
    return [lo[0], lo[1], hi[0] - lo[0], hi[1] - lo[1]];
  }

  function model(data) {
    const scenario = data.deck.deck ? data.deck : { deck: data.deck };
    const deck = scenario.deck;
    const nests = Object.fromEntries(deck.nests.map((nest) => [nest.name, nest]));
    const plates = new Map(deck.plates.map((plate) => [plate.name, plate]));
    let clock = 0;
    const steps = data.trace.filter((line) => !("final" in line)).map((command) => {
      const step = normalize(command);
      step.start = clock;
      clock += command.ms / 1000 + LATENCY;
      step.end = clock;
      return step;
    });
    const stray = steps.flatMap((s) => s.command.events).find((e) => e.plate && !plates.has(e.plate));
    if (stray) throw new Error(`The trace uses plate "${stray.plate}", which this deck does not have. Open the deck of the same protocol.`);
    steps.forEach((step, i) => {
      if (step.op === "C0PP" && !step.failed) step.grips = steps.slice(i + 1).find((s) => s.command.rested.length)?.command.rested[0].plate;
    });
    const racks = (deck.tip_racks || []).map((rack) => ({ ...rack, origin: nests[rack.nest].origin_mm, spots: spots(rack) }));
    const modules = (deck.modules || []).map((module) => ({ name: module.name, box: moduleBox(module, nests) })).filter((m) => m.box);
    const m = { deck, plates, world: scenario.world, steps, racks, modules, total: clock, result: data.result };
    m.max = Math.max(1, ...replay(m, steps.length).peaks);
    return m;
  }

  function replay(m, count) {
    const state = {
      volumes: new Map(), origins: new Map(), used: new Set(), tips: Array(8).fill(false),
      x: null, ys: [], iswap: null, held: null, waste: 0, peaks: [],
    };
    for (const plate of m.deck.plates) {
      state.origins.set(plate.name, plate.origin_mm);
      for (const well of plate.wells) state.volumes.set(`${plate.name}:${well.name}`, well.volume_microliters || 0);
    }
    for (const rack of m.racks) for (const spot of rack.missing || []) state.used.add(`${rack.name}:${spot}`);
    state.peaks.push(...state.volumes.values());
    for (const step of m.steps.slice(0, count)) apply(m, state, step);
    return state;
  }

  function apply(m, state, step) {
    const { command, op } = step;
    const center = (name) => {
      const [x, y] = state.origins.get(name);
      const [w, d] = m.plates.get(name).size_mm;
      return [x + w / 2, y + d / 2];
    };
    for (const event of command.events) {
      const well = `${event.plate}:${event.well}`;
      const spot = `${event.rack}:${event.spot}`;
      if (event.kind === "aspirate") state.volumes.set(well, (state.volumes.get(well) || 0) - event.microliters);
      if (event.kind === "dispense") state.volumes.set(well, (state.volumes.get(well) || 0) + event.microliters);
      if (event.kind === "dispense") state.peaks.push(state.volumes.get(well));
      if (event.kind === "tip_pickup") state.used.add(spot);
      if (event.kind in VERBS && event.channel != null) state.tips[event.channel] = event.kind !== "tip_return";
    }
    if (op === "C0TR" && !step.failed) {
      state.tips.forEach((tip, i) => {
        if (tip && (step.tm ? step.tm[i] : step.ys && shown(step.ys, i))) [state.tips[i], state.waste] = [false, state.waste + 1];
      });
    }
    if (step.x && !ISWAP.has(op)) state.x = step.x;
    if (step.ys) state.ys = step.ys;
    if (step.grips) [state.held, state.iswap] = [step.grips, center(step.grips)];
    for (const rested of command.rested) {
      state.origins.set(rested.plate, rested.origin_mm);
      if (state.held === rested.plate) [state.held, state.iswap] = [null, center(rested.plate)];
    }
    if (op === "C0PG") state.iswap = null;
  }

  function bounds(m) {
    const boxes = [[...WASTE], ...m.modules.map((module) => module.box)];
    for (const carrier of m.deck.carriers || []) boxes.push([...carrier.origin_mm.slice(0, 2), ...carrier.size_mm.slice(0, 2)]);
    for (const plate of m.deck.plates) boxes.push([...plate.origin_mm.slice(0, 2), ...plate.size_mm.slice(0, 2)]);
    for (const rack of m.racks) boxes.push([...rack.origin.slice(0, 2), ...rack.size_mm.slice(0, 2)]);
    const x0 = Math.min(...boxes.map((b) => b[0])) - 12;
    const x1 = Math.max(...boxes.map((b) => b[0] + b[2])) + 12;
    const pad = 0.05 * (x1 - x0);
    const y0 = Math.min(...boxes.map((b) => b[1])) - pad;
    const y1 = Math.max(...boxes.map((b) => b[1] + b[3])) + pad;
    return { x0, y0, x1, y1 };
  }

  function draw(m, plot) {
    const b = bounds(m);
    plot.setAttribute("viewBox", `${b.x0} ${b.y0} ${b.x1 - b.x0} ${b.y1 - b.y0}`);
    const flip = (y) => b.y0 + b.y1 - y;
    const deck = svg(plot, "g", { transform: `matrix(1 0 0 -1 0 ${b.y0 + b.y1})` });
    const labels = svg(plot, "g");
    const label = (text, x, y) => Object.assign(svg(labels, "text", { x, y: flip(y) - 4 }), { textContent: text });
    const footer = (text, x, y) => Object.assign(svg(labels, "text", { x, y: flip(y) + 4, "dominant-baseline": "hanging" }), { textContent: text });
    const rect = (parent, [x, y, w, d], attrs) => svg(parent, "rect", { x, y, width: w, height: d, fill: "none", class: "line", ...attrs });

    for (const obstacle of m.world?.obstacles || []) {
      const box = [obstacle.min[0], obstacle.min[1], obstacle.max[0] - obstacle.min[0], obstacle.max[1] - obstacle.min[1]];
      svg(rect(deck, box, { stroke: C.empty }), "title").textContent = obstacle.name;
    }
    rect(deck, WASTE, { stroke: C.room });
    const waste = label("waste", WASTE[0] + WASTE[2], WASTE[1] + WASTE[3]);
    waste.setAttribute("text-anchor", "end");
    for (const carrier of m.deck.carriers || []) {
      const [x, y] = carrier.origin_mm;
      rect(deck, [x, y, ...carrier.size_mm.slice(0, 2)], { stroke: C.room });
      footer(carrier.name, x, y);
      for (const nest of m.deck.nests.filter((n) => n.carrier === carrier.name && carrier.site_size_mm)) {
        rect(deck, [...nest.origin_mm.slice(0, 2), ...carrier.site_size_mm], { stroke: C.empty, "stroke-dasharray": "2 3" });
      }
    }
    for (const { name, box } of m.modules) {
      svg(rect(deck, box, { fill: C.empty, stroke: C.room }), "title").textContent = name;
      svg(deck, "text", { x: box[0] + box[2] / 2, y: -box[1] - box[3] / 2, transform: "scale(1 -1)", "text-anchor": "middle", "dominant-baseline": "central", class: "inset" }).textContent = name;
    }

    const tips = m.racks.map((rack) => {
      const [x, y] = rack.origin;
      rect(deck, [x, y, ...rack.size_mm.slice(0, 2)], { stroke: C.hair });
      label(rack.name, x, y + rack.size_mm[1]);
      return rack.spots.map((spot) => ({
        key: `${rack.name}:${spot.name}`,
        node: svg(deck, "circle", { cx: x + spot.x, cy: y + spot.y, r: rack.tip_diameter_mm * 0.4, "stroke-width": 0.9, class: "tip" }),
      }));
    }).flat();

    const plates = m.deck.plates.map((plate) => {
      const group = svg(deck, "g");
      rect(group, [0, 0, ...plate.size_mm.slice(0, 2)], { stroke: C.hair, fill: "#fff" });
      const wells = plate.wells.map((well) => {
        const [cx, cy] = well.center_mm;
        const ring = svg(group, "circle", { cx, cy, r: well.radius_mm, fill: C.empty, class: "line" });
        const fill = svg(group, "circle", { cx, cy, r: 0, fill: C.ink });
        return { key: `${plate.name}:${well.name}`, ring, fill, radius: well.radius_mm, title: svg(ring, "title") };
      });
      return { plate, group, wells, label: label(plate.name, 0, 0) };
    });

    const head = svg(deck, "g");
    const arm = svg(head, "line", { y1: b.y0, y2: b.y1, stroke: C.accent, class: "line arm" });
    const channels = Array.from({ length: 8 }, () => svg(head, "circle", { r: 3.2, stroke: C.accent, class: "line" }));
    const iswap = rect(deck, [0, 0, 14, 14], { stroke: C.accent, "stroke-width": 2 });

    return (state, step, at) => {
      const touched = new Set((step?.command.events || []).map((e) => `${e.plate}:${e.well}`));
      for (const tip of tips) {
        const used = state.used.has(tip.key);
        tip.node.setAttribute("fill", used ? C.empty : "#fff");
        tip.node.setAttribute("stroke", used ? "none" : C.faint);
      }
      for (const { plate, group, wells, label: text } of plates) {
        const held = state.held === plate.name && at.iswap;
        const [x, y] = held
          ? [at.iswap[0] - plate.size_mm[0] / 2, at.iswap[1] - plate.size_mm[1] / 2]
          : state.origins.get(plate.name);
        group.setAttribute("transform", `translate(${x} ${y}) rotate(${plate.yaw_degrees || 0})`);
        group.setAttribute("opacity", held ? 0.6 : 1);
        text.setAttribute("x", x);
        text.setAttribute("y", flip(y + plate.size_mm[1]) - 4);
        for (const well of wells) {
          const volume = Math.max(0, state.volumes.get(well.key));
          well.fill.setAttribute("r", well.radius * Math.sqrt(Math.min(1, volume / m.max)));
          well.ring.setAttribute("stroke", touched.has(well.key) ? (step.failed ? C.bad : C.accent) : "none");
          well.title.textContent = `${well.key} ${volume.toFixed(1)} µL`;
        }
      }
      head.setAttribute("visibility", at.x == null ? "hidden" : "visible");
      arm.setAttribute("x1", at.x ?? 0);
      arm.setAttribute("x2", at.x ?? 0);
      channels.forEach((channel, i) => {
        channel.setAttribute("visibility", at.x != null && shown(at.ys, i) ? "visible" : "hidden");
        channel.setAttribute("cx", at.x ?? 0);
        channel.setAttribute("cy", at.ys[i] || 0);
        channel.setAttribute("fill", state.tips[i] ? C.accent : "#fff");
      });
      iswap.setAttribute("visibility", at.iswap ? "visible" : "hidden");
      if (at.iswap) {
        iswap.setAttribute("x", at.iswap[0] - 7);
        iswap.setAttribute("y", at.iswap[1] - 7);
      }
      waste.textContent = state.waste ? `waste · ${state.waste} tips` : "waste";
    };
  }

  function describe(step) {
    const groups = new Map();
    for (const event of step.command.events.filter((e) => VERBS[e.kind])) {
      const amount = event.microliters == null ? "" : ` ${event.microliters.toFixed(1)} µL`;
      const what = VERBS[event.kind] + amount;
      const places = groups.get(what) || new Map();
      const [name, spot] = event.plate ? [event.plate, event.well] : [event.rack, event.spot];
      groups.set(what, places.set(name, [...(places.get(name) || []), spot]));
    }
    return [...groups].map(([what, places]) => `${what} · ${[...places].map(([name, spots]) => `${name} ${spots.join(" ")}`).join(", ")}`);
  }

  function summary(m) {
    const result = m.result;
    const pickups = m.steps.flatMap((s) => s.command.events).filter((e) => e.kind === "tip_pickup").length;
    const facts = [["Commands", m.steps.length], ["Tips", pickups]];
    if (!result || result.valid) facts.unshift(["Seconds", (result?.seconds ?? m.total).toFixed(1)]);
    if (result) facts.unshift(["Result", result.valid ? "Valid" : "Invalid"]);
    return el("dl", { className: "summary" }, ...facts.map(([key, value]) =>
      el("div", {}, el("dt", { textContent: key }), el("dd", { textContent: value, className: value === "Invalid" ? "bad" : "" }))));
  }

  function failures(m, seek) {
    const items = m.result
      ? (m.result.failures || []).map((f) => (typeof f === "string" ? f : f.message || JSON.stringify(f)))
      : m.steps.filter((s) => s.failed).map((s) => `line ${s.command.line}: ${s.command.reply}`);
    const entry = (text) => {
      const line = Number(text.match(/line (\d+)/i)?.[1]);
      const step = m.steps.find((s) => s.command.line === line);
      return el("li", {}, step ? el("button", { type: "button", textContent: text, onclick: () => seek(step.end) }) : text);
    };
    const list = el("ul", { className: "failures" }, ...items.slice(0, FAILURES).map(entry));
    if (items.length > FAILURES) {
      const rest = el("ul", {}, ...items.slice(FAILURES).map(entry));
      list.append(el("li", {}, el("details", {}, el("summary", { textContent: `${items.length - FAILURES} more` }), rest)));
    }
    return list;
  }

  function scale(plot) {
    const box = plot.viewBox.baseVal;
    const update = () => {
      const { width, height } = plot.getBoundingClientRect();
      const u = Math.max(box.width / (width || 1), box.height / (height || 1));
      plot.style.setProperty("--u", u);
    };
    new ResizeObserver(update).observe(plot);
    update();
  }

  function render(root, data) {
    if (!data.deck || !Array.isArray(data.trace)) return root.replaceChildren(el("p", { className: "meta", textContent: "No trace to show." }));
    const m = model(data);
    const plot = svg(document.createDocumentFragment(), "svg", { class: "plot", role: "img", "aria-label": "Deck" });
    const update = draw(m, plot);
    const range = el("input", { type: "range", min: 0, max: m.total, step: "any", "aria-label": "Time" });
    const strip = svg(document.createDocumentFragment(), "svg", { viewBox: "0 0 1000 6", preserveAspectRatio: "none", "aria-hidden": "true" });
    for (const step of m.steps.filter((s) => s.failed)) {
      svg(strip, "rect", { x: (step.start / m.total) * 1000, width: Math.max(2, ((step.end - step.start) / m.total) * 1000), height: 6, fill: C.bad });
    }
    const time = el("span", { className: "time" });
    const play = el("button", { type: "button", textContent: "Play" });
    const speed = el("select", { "aria-label": "Speed" }, ...SPEEDS.map((s) => el("option", { value: s, textContent: `${s}×` })));
    speed.value = 20;
    const command = el("div", { className: "command" });
    let t = 0;
    let playing = false;

    const seek = (value) => {
      t = Math.min(m.total, Math.max(0, value));
      const started = m.steps.findLastIndex((s) => s.start < t) + 1;
      const done = m.steps.findLastIndex((s) => s.end <= t + 1e-9) + 1;
      const step = m.steps[started - 1];
      const state = replay(m, done);
      const before = done < started ? state : replay(m, Math.max(0, started - 1));
      const after = done < started ? replay(m, started) : state;
      const fraction = step ? Math.min(1, (t - step.start) / (step.end - step.start)) : 1;
      const lerp = (a, b) => (a == null || b == null ? b ?? a : a + (b - a) * fraction);
      const iswap = before.iswap && after.iswap ? [0, 1].map((k) => lerp(before.iswap[k], after.iswap[k])) : after.iswap ?? before.iswap;
      update(state, step, { x: lerp(before.x, after.x), ys: after.ys.map((y, i) => lerp(before.ys[i] || null, y)), iswap });
      range.value = t;
      time.textContent = `${t.toFixed(1)} / ${m.total.toFixed(1)} s`;
      command.replaceChildren(...describeStep(step));
    };

    const describeStep = (step) => {
      if (!step) return [el("p", { textContent: "Start" })];
      const c = step.command;
      const head = el("p", {}, el("strong", { textContent: `Line ${c.line} · ${OPS[step.op] || step.op}` }), ` · ${(c.ms / 1000).toFixed(2)} s`);
      const out = [head, ...describe(step).map((text) => el("p", { textContent: text }))];
      if (step.failed) out.push(el("p", { className: "bad", textContent: `Failed · reply ${c.reply}` }));
      for (const e of c.events.filter((e) => e.kind === "collision")) {
        out.push(el("p", { className: "bad", textContent: `Collision · ${e.part} hit ${e.obstacle}` }));
      }
      if (c.wire) out.push(el("p", { className: "wire", textContent: c.wire }));
      return out;
    };

    const tick = (last) => (now) => {
      if (!playing || !plot.isConnected) return;
      seek(t + ((now - last) / 1000) * Number(speed.value));
      if (t >= m.total) return stop();
      requestAnimationFrame(tick(now));
    };
    const stop = () => {
      playing = false;
      play.textContent = "Play";
    };
    play.onclick = () => {
      if (playing) return stop();
      if (t >= m.total) seek(0);
      playing = true;
      play.textContent = "Pause";
      requestAnimationFrame((now) => requestAnimationFrame(tick(now)));
    };
    const jump = (direction) => () => {
      stop();
      const ends = [0, ...m.steps.map((s) => s.end)];
      seek(direction > 0 ? ends.find((e) => e > t + 1e-6) ?? m.total : ends.findLast((e) => e < t - 1e-6) ?? 0);
    };
    range.oninput = () => {
      stop();
      seek(Number(range.value));
    };

    const controls = el("div", { className: "controls" },
      el("div", { className: "scrub" }, range, strip),
      play,
      el("button", { type: "button", textContent: "‹", "aria-label": "Previous command", onclick: jump(-1) }),
      el("button", { type: "button", textContent: "›", "aria-label": "Next command", onclick: jump(1) }),
      speed,
      time);
    root.replaceChildren(el("div", { className: "viewer" }, summary(m), failures(m, (v) => { stop(); seek(v); }), plot, controls, command));
    scale(plot);
    seek(m.steps.find((s) => s.failed)?.end ?? m.total);
  }

  function parse(text) {
    try {
      return JSON.parse(text);
    } catch {
      return text.split("\n").filter((line) => line.trim()).map((line) => JSON.parse(line));
    }
  }

  function classify(object) {
    if (Array.isArray(object)) return { trace: object };
    if (Array.isArray(object.trace) && object.deck) return object;
    if (typeof object.deck === "object" || object.plates) return { deck: object };
    if (typeof object.deck === "string" && object.steps) return { protocol: object };
    if ("valid" in object) return { result: object };
    return {};
  }

  window.renderHamilton = render;
  const root = document.getElementById("viewer");
  if (root && window.VIEWER_DATA) render(root, window.VIEWER_DATA);
  const input = document.getElementById("files");
  const status = document.getElementById("status");
  if (!root || !input) return;
  const loaded = {};
  const names = {};

  const say = (text, error) => {
    status.className = error ? "meta error" : "meta";
    status.textContent = text;
  };

  async function open(files) {
    const batch = {};
    const unknown = [];
    for (const file of files) {
      let found = {};
      try {
        found = classify(parse(await file.text()));
      } catch {}
      if (!Object.keys(found).length) unknown.push(file.name);
      for (const kind of Object.keys(found)) names[kind] = file.name;
      Object.assign(batch, found);
    }
    if (batch.trace && !batch.result) delete loaded.result;
    Object.assign(loaded, batch);
    for (const code of document.querySelectorAll("[data-kind]")) {
      const kind = code.dataset.kind;
      code.classList.toggle("have", kind in loaded);
      if (kind in loaded) code.textContent = names[kind];
    }
    if (unknown.length) return say(`${unknown.join(", ")}: not a deck, trace or report.`, true);
    if (!loaded.deck) return say(loaded.protocol ? `That is the protocol. Add ${loaded.protocol.deck} instead.` : "Now add the deck.", true);
    if (!loaded.trace) return say("Now add the trace.", true);
    try {
      render(root, loaded);
      say("");
    } catch (error) {
      root.replaceChildren();
      say(error.message, true);
    }
  }

  input.onchange = () => {
    const files = [...input.files];
    input.value = "";
    open(files);
  };
  document.addEventListener("dragover", (event) => {
    event.preventDefault();
    document.body.classList.add("dropping");
  });
  document.addEventListener("dragleave", (event) => {
    if (!event.relatedTarget) document.body.classList.remove("dropping");
  });
  document.addEventListener("drop", (event) => {
    event.preventDefault();
    document.body.classList.remove("dropping");
    open(event.dataTransfer.files);
  });
})();
