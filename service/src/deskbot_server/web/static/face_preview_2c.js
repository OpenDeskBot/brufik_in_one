(function (global) {
  "use strict";

  const LAYERS = ["eye_l", "eye_r", "nose", "mouth", "extra"];
  const LAYER_COLORS = {
    eye_l: "#f4f4ef",
    eye_r: "#f4f4ef",
    nose: "#ffd23f",
    mouth: "#ff6700",
    extra: "#ffd23f",
  };

  const FALLBACK_SCENE = {
    name: "fallback",
    title: "待机",
    frames: [{
      elements: {
        eye_l: [{ shape: "ellipse_fill", x: 86, y: 97, rw: 17, rh: 17 }],
        eye_r: [{ shape: "ellipse_fill", x: 198, y: 97, rw: 17, rh: 17 }],
        nose: [],
        mouth: [{ shape: "round_rect_outline", x: 122, y: 156, w: 40, h: 12, radius: 6 }],
        extra: [],
      },
    }],
  };

  function num(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function isOutline(shape) {
    return String(shape || "").toLowerCase().indexOf("outline") >= 0;
  }

  function strokeWidth(p) {
    return num(p.stroke_width != null ? p.stroke_width : p.sw, 2);
  }

  function shapeToSvg(p, layer) {
    if (!p || typeof p !== "object") return "";
    const shape = String(p.shape || "").toLowerCase();
    const color = LAYER_COLORS[layer] || "#f4f4ef";
    const sw = strokeWidth(p);
    const fill = isOutline(shape) ? "none" : color;
    const stroke = isOutline(shape) || shape === "line" ? color : "none";

    if (shape === "circle" || shape === "circle_fill" || shape === "circle_outline") {
      return `<circle cx="${num(p.x, 0)}" cy="${num(p.y, 0)}" r="${num(p.r, 1)}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
    }
    if (shape === "ellipse" || shape === "ellipse_fill" || shape === "ellipse_outline") {
      return `<ellipse cx="${num(p.x, 0)}" cy="${num(p.y, 0)}" rx="${num(p.rw != null ? p.rw : p.r, 1)}" ry="${num(p.rh != null ? p.rh : p.r, 1)}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
    }
    if (shape === "rect" || shape === "rect_fill" || shape === "rect_outline") {
      return `<rect x="${num(p.x, 0)}" y="${num(p.y, 0)}" width="${num(p.w, 1)}" height="${num(p.h, 1)}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
    }
    if (shape === "round_rect" || shape === "round_rect_fill" || shape === "round_rect_outline") {
      return `<rect x="${num(p.x, 0)}" y="${num(p.y, 0)}" width="${num(p.w, 1)}" height="${num(p.h, 1)}" rx="${num(p.radius, 1)}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
    }
    if (shape === "line") {
      return `<line x1="${num(p.x1, 0)}" y1="${num(p.y1, 0)}" x2="${num(p.x2, 0)}" y2="${num(p.y2, 0)}" stroke="${color}" stroke-width="${sw}" stroke-linecap="round"/>`;
    }
    if (shape === "pixel") {
      return `<rect x="${num(p.x, 0)}" y="${num(p.y, 0)}" width="1" height="1" fill="${color}"/>`;
    }
    if (shape === "triangle" || shape === "triangle_fill" || shape === "triangle_outline") {
      const points = [
        `${num(p.x0, 0)},${num(p.y0, 0)}`,
        `${num(p.x1, 0)},${num(p.y1, 0)}`,
        `${num(p.x2, 0)},${num(p.y2, 0)}`,
      ].join(" ");
      return `<polygon points="${points}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
    }
    if (shape === "text") {
      return `<text x="${num(p.x, 0)}" y="${num(p.y, 0)}" fill="${color}" font-size="${num(p.size, 16)}" text-anchor="middle">${esc(p.text || "")}</text>`;
    }
    return "";
  }

  function frameElements(scene, frameIndex) {
    const s = scene && typeof scene === "object" ? scene : FALLBACK_SCENE;
    const frames = Array.isArray(s.frames) && s.frames.length ? s.frames : FALLBACK_SCENE.frames;
    const idx = Math.max(0, Math.min(Math.floor(num(frameIndex, 0)), frames.length - 1));
    const frame = frames[idx] || null;
    const elements = frame && (frame.elements || (frame.anim && frame.anim.elements));
    return elements && typeof elements === "object" ? elements : FALLBACK_SCENE.frames[0].elements;
  }

  function sceneToSvg(scene, frameIndex) {
    const elements = frameElements(scene, frameIndex);
    let out = "";
    for (const layer of LAYERS) {
      const rows = Array.isArray(elements[layer]) ? elements[layer] : [];
      for (const p of rows) out += shapeToSvg(p, layer);
    }
    return out;
  }

  function frameCount(scene) {
    const frames = scene && Array.isArray(scene.frames) ? scene.frames : [];
    return Math.max(1, frames.length || FALLBACK_SCENE.frames.length);
  }

  function frameMs(scene, frameIndex) {
    const s = scene && typeof scene === "object" ? scene : FALLBACK_SCENE;
    const frames = Array.isArray(s.frames) && s.frames.length ? s.frames : FALLBACK_SCENE.frames;
    const idx = Math.max(0, Math.min(Math.floor(num(frameIndex, 0)), frames.length - 1));
    return Math.max(40, num((frames[idx] || {}).ms, 500));
  }

  function findScene(scenes, name) {
    const want = String(name || "").trim().toLowerCase();
    if (!want || !Array.isArray(scenes)) return null;
    return scenes.find((s) => String((s && s.name) || "").trim().toLowerCase() === want) || null;
  }

  function pickScene(scenes, map, mood) {
    const rows = Array.isArray(scenes) ? scenes : [];
    const mapping = map && typeof map === "object" ? map : {};
    return (
      findScene(rows, mapping[mood || "idle"]) ||
      findScene(rows, "idle") ||
      findScene(rows, "default") ||
      rows[0] ||
      FALLBACK_SCENE
    );
  }

  global.DeskbotFacePreview = {
    sceneToSvg,
    frameElements,
    frameCount,
    frameMs,
    findScene,
    pickScene,
    fallbackScene: FALLBACK_SCENE,
  };
})(window);
