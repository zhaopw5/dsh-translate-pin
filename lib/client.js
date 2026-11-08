window.__ModuleLoader__.load({
	id: "dsh-translate-pin",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;

		/** 依赖的 Client 服务：Connection（自定义 RPC 通道）。 */
		const inject = ["connection"];

		const CHANNEL = "/translate-pin";
		const MAX_SELECTION = 4000;

		const CSS = `
.dtp-root { --dtp-bg: rgba(24,25,30,.96); --dtp-fg: #eceef3; --dtp-muted: #9b9da7; --dtp-faint: #6f717b;
  --dtp-border: rgba(255,255,255,.10); --dtp-hover: rgba(255,255,255,.07); --dtp-accent: #5b7cfa;
  --dtp-shadow: 0 18px 48px rgba(0,0,0,.45), 0 2px 8px rgba(0,0,0,.35);
  position: fixed; inset: 0; pointer-events: none; z-index: 2147483000;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif; }
.dtp-root.dtp-light { --dtp-bg: rgba(255,255,255,.97); --dtp-fg: #1c1d22; --dtp-muted: #5f6270;
  --dtp-faint: #8b8e9a; --dtp-border: rgba(15,17,24,.12); --dtp-hover: rgba(15,17,24,.06);
  --dtp-shadow: 0 18px 48px rgba(15,17,24,.22), 0 2px 8px rgba(15,17,24,.12); }

.dtp-pill { position: absolute; pointer-events: auto; display: none; align-items: center; justify-content: center;
  gap: 4px; width: 30px; height: 30px; border-radius: 9px; background: var(--dtp-accent); color: #fff;
  font-size: 13px; font-weight: 600; cursor: pointer; box-shadow: 0 6px 18px rgba(77,107,254,.45);
  user-select: none; transition: transform .12s ease, filter .12s ease; }
.dtp-pill:hover { transform: translateY(-1px); filter: brightness(1.08); }
.dtp-pill span { font-size: 12px; }

.dtp-card { position: absolute; pointer-events: auto; display: none; width: 392px; max-width: calc(100vw - 24px);
  background: var(--dtp-bg); color: var(--dtp-fg); border: 1px solid var(--dtp-border); border-radius: 14px;
  box-shadow: var(--dtp-shadow); backdrop-filter: blur(18px) saturate(1.1);
  -webkit-backdrop-filter: blur(18px) saturate(1.1); overflow: hidden; font-size: 13.5px; line-height: 1.65;
  animation: dtp-in .14s ease-out; }
@keyframes dtp-in { from { opacity: 0; transform: translateY(4px) scale(.985); } to { opacity: 1; transform: none; } }
.dtp-head { display: flex; align-items: center; gap: 8px; padding: 10px 10px 8px 14px; }
.dtp-lang { font-size: 12px; color: var(--dtp-muted); font-weight: 600; letter-spacing: .2px; }
.dtp-chip { font-size: 10.5px; color: var(--dtp-faint); border: 1px solid var(--dtp-border); border-radius: 999px;
  padding: 1px 7px; line-height: 1.5; }
.dtp-grow { flex: 1; }
.dtp-icon { width: 26px; height: 26px; border: 0; border-radius: 7px; background: transparent; color: var(--dtp-muted);
  font-size: 16px; line-height: 1; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
.dtp-icon:hover { background: var(--dtp-hover); color: var(--dtp-fg); }
.dtp-body { padding: 2px 14px 12px; max-height: 320px; overflow: auto; white-space: pre-wrap; word-break: break-word; }
.dtp-body::-webkit-scrollbar { width: 8px; }
.dtp-body::-webkit-scrollbar-thumb { background: var(--dtp-border); border-radius: 8px; }
.dtp-orig { display: none; margin: 0 14px; padding: 9px 10px; border-radius: 9px; background: var(--dtp-hover);
  color: var(--dtp-muted); font-size: 12px; line-height: 1.55; max-height: 160px; overflow: auto; white-space: pre-wrap; }
.dtp-orig.dtp-show { display: block; }
.dtp-foot { display: flex; align-items: center; gap: 2px; padding: 8px 10px 10px 10px; border-top: 1px solid var(--dtp-border); margin-top: 10px; }
.dtp-btn { border: 0; background: transparent; color: var(--dtp-muted); font-size: 12.5px; padding: 5px 9px;
  border-radius: 8px; cursor: pointer; font-family: inherit; }
.dtp-btn:hover { background: var(--dtp-hover); color: var(--dtp-fg); }
.dtp-btn.dtp-primary { color: var(--dtp-accent); font-weight: 600; }
.dtp-btn:disabled { opacity: .45; cursor: default; }
.dtp-skeleton { padding: 10px 14px 16px; }
.dtp-skline { height: 10px; border-radius: 6px; background: var(--dtp-hover); margin: 9px 0;
  animation: dtp-pulse 1.2s ease-in-out infinite; }
.dtp-skline:nth-child(2) { width: 78%; animation-delay: .12s; }
.dtp-skline:nth-child(3) { width: 55%; animation-delay: .24s; }
@keyframes dtp-pulse { 0%,100% { opacity: .55; } 50% { opacity: 1; } }
.dtp-err { padding: 6px 14px 14px; color: #ff7b7b; font-size: 12.5px; }
.dtp-light .dtp-err { color: #c0392b; }

.dtp-launcher { position: absolute; pointer-events: auto; right: 18px; bottom: 116px; height: 38px;
  display: flex; align-items: center; gap: 7px; padding: 0 13px; border-radius: 999px;
  background: var(--dtp-bg); border: 1px solid var(--dtp-border); color: var(--dtp-fg);
  box-shadow: var(--dtp-shadow); cursor: pointer; font-size: 12.5px; opacity: .78;
  transition: opacity .15s ease, transform .15s ease; user-select: none; }
.dtp-launcher:hover { opacity: 1; transform: translateY(-1px); }
.dtp-launcher svg { width: 15px; height: 15px; }
.dtp-launcher kbd { font: inherit; font-size: 10px; color: var(--dtp-faint); border: 1px solid var(--dtp-border);
  border-radius: 5px; padding: 0 4px; margin-left: 2px; }

.dtp-toast { position: absolute; left: 50%; bottom: 34px; transform: translateX(-50%) translateY(6px);
  background: rgba(20,21,26,.94); color: #fff; border: 1px solid rgba(255,255,255,.10); border-radius: 10px;
  padding: 8px 14px; font-size: 12.5px; opacity: 0; pointer-events: none; transition: opacity .18s ease, transform .18s ease;
  box-shadow: 0 10px 26px rgba(0,0,0,.35); max-width: 70vw; }
.dtp-toast.dtp-show { opacity: 1; transform: translateX(-50%) translateY(0); }
.dtp-light .dtp-toast { background: rgba(255,255,255,.98); color: #1c1d22; border-color: rgba(15,17,24,.12); }
.dtp-spin { width: 12px; height: 12px; border: 2px solid var(--dtp-border); border-top-color: var(--dtp-accent);
  border-radius: 50%; animation: dtp-spin .7s linear infinite; display: inline-block; vertical-align: -2px; margin-right: 6px; }
@keyframes dtp-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .dtp-card, .dtp-pill, .dtp-launcher, .dtp-toast { animation: none; transition: none; }
  .dtp-skline, .dtp-spin { animation: none; }
}
`;

		function isEditable(node) {
			if (!node) return false;
			const element = node.nodeType === 1 ? node : node.parentElement;
			if (!element) return false;
			if (element.closest("input, textarea, select")) return true;
			if (element.closest('[contenteditable="true"], [contenteditable=""], [contenteditable="plaintext-only"]')) return true;
			return false;
		}

		function el(tag, className, text) {
			const node = document.createElement(tag);
			if (className) node.className = className;
			if (text !== undefined) node.textContent = text;
			return node;
		}

		function apply(ctx) {
			try {
				applyInner(ctx);
			} catch (error) {
				// 客户端插件在 boot 阶段抛错会让整个页面停在 “Failed to load plugins”，
				// 这里吞掉异常，只破坏本插件，不影响 DSH 启动。
				console.error("[translate-pin] apply failed", error);
			}
		}

		function applyInner(ctx) {
			if (window.__DTP_ACTIVE__) {
				try { window.__DTP_ACTIVE__.dispose(); } catch {}
			}

			const root = el("div", "dtp-root");
			const style = document.createElement("style");
			style.textContent = CSS;
			document.head.appendChild(style);

			const pill = el("div", "dtp-pill", "译");
			pill.title = "翻译选中文本";
			const card = el("div", "dtp-card");
			const launcher = el("div", "dtp-launcher");
			launcher.innerHTML =
				'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
				'<path d="M3 8.5A2.5 2.5 0 0 1 5.5 6h1.2a2 2 0 0 0 1.7-.95l.55-.9A2 2 0 0 1 10.65 3h2.7a2 2 0 0 1 1.7 1.05l.55.9A2 2 0 0 0 17.3 6h1.2A2.5 2.5 0 0 1 21 8.5v9A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5z"/>' +
				'<circle cx="12" cy="12.5" r="3.4"/></svg><span>截图翻译</span><kbd>Ctrl+Shift+S</kbd>';
			const toast = el("div", "dtp-toast");

			root.append(pill, card, launcher, toast);
			document.body.appendChild(root);

			if (isLightTheme()) root.classList.add("dtp-light");

			let state = {
				text: "",
				translation: "",
				source: "",
				target: "",
				provider: "",
				kind: "text",
				anchor: null,
				busy: false,
				showOriginal: false,
				toastTimer: 0,
				targetDefault: "zh-Hans",
				globalHotkey: false,
				capturing: false,
			};

			function isAlreadyTarget(text) {
				const target = String(state.targetDefault || "").toLowerCase();
				if (!target.startsWith("zh")) return false;
				const compact = String(text || "").replace(/\s+/g, "");
				if (!compact) return false;
				const han = (compact.match(/[\u3400-\u9fff]/g) || []).length;
				const kana = (compact.match(/[\u3040-\u30ff]/g) || []).length;
				return han / compact.length > 0.6 && kana / compact.length < 0.05;
			}

			// ---------------------------------------------------------- RPC
			async function callApi(endpoint, payload, timeoutMs = 30_000) {
				const controller = new AbortController();
				const timer = setTimeout(() => controller.abort(), timeoutMs);
				try {
					const response = await fetch(`api/translate-pin/${endpoint}`, {
						method: "POST",
						headers: { "content-type": "application/json" },
						body: JSON.stringify(payload || {}),
						signal: controller.signal,
					});
					if (!response.ok) throw new Error(`请求失败 (HTTP ${response.status})`);
					const data = await response.json();
					if (!data || data.ok !== true) {
						throw new Error((data && data.error && data.error.message) || "请求失败");
					}
					return data.value;
				} finally {
					clearTimeout(timer);
				}
			}

			// ---------------------------------------------------------- 提示
			function showToast(message, ms = 2200) {
				toast.textContent = message;
				toast.classList.add("dtp-show");
				clearTimeout(state.toastTimer);
				state.toastTimer = setTimeout(() => toast.classList.remove("dtp-show"), ms);
			}

			// ---------------------------------------------------------- 选区浮标
			function hidePill() {
				pill.style.display = "none";
			}

			function currentSelection() {
				const selection = window.getSelection();
				if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
				const text = selection.toString().replace(/\u00a0/g, " ").trim();
				if (!text) return null;
				const range = selection.getRangeAt(0);
				if (isEditable(range.commonAncestorContainer)) return null;
				if (range.commonAncestorContainer.parentElement && range.commonAncestorContainer.parentElement.closest(".dtp-root")) return null;
				const rects = range.getClientRects();
				const rect = rects.length > 0 ? rects[rects.length - 1] : range.getBoundingClientRect();
				if (!rect || (rect.width === 0 && rect.height === 0)) return null;
				return { text, rect, range };
			}

			function showPillAt(rect) {
				const left = Math.min(window.innerWidth - 42, Math.max(8, rect.right + 6));
				const top = Math.min(window.innerHeight - 42, Math.max(6, rect.bottom + 5));
				pill.style.left = left + "px";
				pill.style.top = top + "px";
				pill.style.display = "flex";
			}

			function onMouseUp(event) {
				if (event.target && event.target.closest && event.target.closest(".dtp-root")) return;
				setTimeout(() => {
					const selection = currentSelection();
					if (!selection) {
						hidePill();
						return;
					}
					if (selection.text.length > MAX_SELECTION) {
						hidePill();
						return;
					}
					if (isAlreadyTarget(selection.text)) {
						hidePill();
						return;
					}
					state.selText = selection.text;
					state.selRect = selection.rect;
					showPillAt(selection.rect);
				}, 10);
			}

			function onSelectionChange() {
				const selection = window.getSelection();
				if (!selection || selection.isCollapsed) hidePill();
			}

			// ---------------------------------------------------------- 卡片
			function closeCard() {
				card.style.display = "none";
				state.busy = false;
			}

			function placeCard(anchorRect, height) {
				const margin = 12;
				const width = Math.min(392, window.innerWidth - margin * 2);
				let left = anchorRect ? anchorRect.left : window.innerWidth - width - 20;
				left = Math.max(margin, Math.min(left, window.innerWidth - width - margin));
				let top;
				if (anchorRect) {
					top = anchorRect.bottom + 10;
					if (top + height > window.innerHeight - margin) {
						top = Math.max(margin, anchorRect.top - height - 10);
					}
				} else {
					top = Math.max(margin, window.innerHeight - height - 150);
				}
				card.style.left = left + "px";
				card.style.top = top + "px";
				card.style.width = width + "px";
			}

			function renderLoading(anchorRect) {
				state.busy = true;
				card.innerHTML =
					'<div class="dtp-head"><span class="dtp-lang">正在翻译</span><span class="dtp-grow"></span>' +
					'<button class="dtp-icon" data-act="close" title="关闭">×</button></div>' +
					'<div class="dtp-skeleton"><div class="dtp-skline"></div><div class="dtp-skline"></div><div class="dtp-skline"></div></div>';
				card.style.display = "block";
				bindCard();
				requestAnimationFrame(() => placeCard(anchorRect, card.offsetHeight));
			}

			function renderError(message, anchorRect) {
				state.busy = false;
				card.innerHTML =
					'<div class="dtp-head"><span class="dtp-lang">翻译失败</span><span class="dtp-grow"></span>' +
					'<button class="dtp-icon" data-act="close" title="关闭">×</button></div>' +
					'<div class="dtp-err"></div>' +
					'<div class="dtp-foot"><button class="dtp-btn" data-act="retry">重试</button>' +
					'<button class="dtp-btn" data-act="close">关闭</button></div>';
				card.querySelector(".dtp-err").textContent = message;
				card.style.display = "block";
				bindCard();
				requestAnimationFrame(() => placeCard(anchorRect, card.offsetHeight));
			}

			function renderResult(anchorRect) {
				state.busy = false;
				const pair = `${langName(state.source)} → ${langName(state.target)}`;
				card.innerHTML =
					'<div class="dtp-head"><span class="dtp-lang"></span>' +
					(state.provider ? '<span class="dtp-chip"></span>' : "") +
					'<span class="dtp-grow"></span><button class="dtp-icon" data-act="close" title="关闭">×</button></div>' +
					'<div class="dtp-orig"></div>' +
					'<div class="dtp-body"></div>' +
					'<div class="dtp-foot">' +
					'<button class="dtp-btn dtp-primary" data-act="pin">钉在桌面</button>' +
					'<button class="dtp-btn" data-act="copy">复制</button>' +
					(state.kind === "screenshot" ? '<button class="dtp-btn" data-act="again">重新截图</button>' : "") +
					'<span class="dtp-grow"></span>' +
					(state.text ? '<button class="dtp-btn" data-act="orig">原文</button>' : "") +
					'</div>';
				card.querySelector(".dtp-lang").textContent = pair;
				if (state.provider) card.querySelector(".dtp-chip").textContent = state.provider;
				card.querySelector(".dtp-body").textContent = state.translation;
				const original = card.querySelector(".dtp-orig");
				original.textContent = state.text;
				if (state.showOriginal) original.classList.add("dtp-show");
				card.style.display = "block";
				bindCard();
				requestAnimationFrame(() => placeCard(anchorRect, card.offsetHeight));
			}

			function bindCard() {
				card.querySelectorAll("[data-act]").forEach((button) => {
					button.addEventListener("click", (event) => {
						event.preventDefault();
						event.stopPropagation();
						onAction(button.dataset.act);
					});
				});
			}

			function onAction(action) {
				switch (action) {
					case "close":
						closeCard();
						break;
					case "copy":
						copyText(state.translation).then(
							() => showToast("已复制译文"),
							() => showToast("复制失败，请手动选择"),
						);
						break;
					case "pin":
						pinResult();
						break;
					case "orig": {
						state.showOriginal = !state.showOriginal;
						const node = card.querySelector(".dtp-orig");
						if (node) node.classList.toggle("dtp-show", state.showOriginal);
						requestAnimationFrame(() => placeCard(state.anchor, card.offsetHeight));
						break;
					}
					case "retry":
						if (state.text) translate(state.text, state.anchor, state.kind);
						break;
					case "again":
						startCapture();
						break;
					default:
						break;
				}
			}

			async function copyText(text) {
				try {
					await navigator.clipboard.writeText(text);
					return;
				} catch {}
				const area = document.createElement("textarea");
				area.value = text;
				area.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0;";
				document.body.appendChild(area);
				area.select();
				document.execCommand("copy");
				area.remove();
			}

			// ---------------------------------------------------------- 翻译
			async function translate(text, anchorRect, kind) {
				hidePill();
				state.text = text;
				state.kind = kind || "text";
				state.anchor = anchorRect || null;
				renderLoading(anchorRect);
				try {
					const value = await callApi("translate", { text });
					state.translation = value.text;
					state.source = value.source || "auto";
					state.target = value.target || "zh-Hans";
					state.provider = value.provider || "";
					renderResult(anchorRect);
				} catch (error) {
					renderError(error && error.message ? error.message : String(error), anchorRect);
				}
			}

			async function pinResult() {
				try {
					await callApi("pin", {
						original: state.text,
						translated: state.translation,
						source: state.source,
						target: state.target,
						provider: state.provider,
						kind: state.kind,
					}, 30_000);
					showToast("已钉在桌面，可拖动");
				} catch (error) {
					showToast(error && error.message ? error.message : "钉住失败");
				}
			}

			// ---------------------------------------------------------- 截图
			async function startCapture() {
				if (state.capturing) return;
				state.capturing = true;
				closeCard();
				hidePill();
				showToast("拖拽选择要翻译的区域，Esc 取消", 60_000);
				try {
					const shot = await callApi("capture", {}, 600_000);
					toast.classList.remove("dtp-show");
					showToast(`已翻译并贴在原区域${shot && shot.provider ? " · " + shot.provider : ""}`);
				} catch (error) {
					toast.classList.remove("dtp-show");
					const message = error && error.message ? error.message : String(error);
					if (message.includes("取消") || message.includes("cancel")) {
						showToast("已取消");
						return;
					}
					showToast(message);
				} finally {
					state.capturing = false;
				}
			}

			let lastPointer = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
			function onPointerMove(event) {
				lastPointer = { x: event.clientX, y: event.clientY };
			}
			function lastRect() {
				return { left: lastPointer.x, right: lastPointer.x, top: lastPointer.y, bottom: lastPointer.y };
			}

			function onKeyDown(event) {
				if (event.ctrlKey && event.shiftKey && !event.altKey) {
					const key = event.key.toLowerCase();
					if (key === "s") {
						if (state.globalHotkey) return;
						event.preventDefault();
						startCapture();
						return;
					}
					if (key === "t") {
						const selection = currentSelection();
						if (selection) {
							event.preventDefault();
							hidePill();
							translate(selection.text, selection.rect, "text");
						}
						return;
					}
				}
				if (event.key === "Escape") {
					if (card.style.display === "block") {
						event.preventDefault();
						closeCard();
					}
					hidePill();
				}
			}

			function onPointerDown(event) {
				if (event.target && event.target.closest && event.target.closest(".dtp-root")) return;
				hidePill();
			}

			pill.addEventListener("mousedown", (event) => {
				event.preventDefault();
				event.stopPropagation();
				const text = state.selText;
				const rect = state.selRect;
				hidePill();
				if (text) translate(text, rect, "text");
			});

			launcher.addEventListener("click", (event) => {
				event.preventDefault();
				event.stopPropagation();
				startCapture();
			});

			document.addEventListener("mouseup", onMouseUp, true);
			document.addEventListener("selectionchange", onSelectionChange);
			document.addEventListener("pointerdown", onPointerDown, true);
			document.addEventListener("pointermove", onPointerMove, true);
			document.addEventListener("keydown", onKeyDown, true);

			function dispose() {
				document.removeEventListener("mouseup", onMouseUp, true);
				document.removeEventListener("selectionchange", onSelectionChange);
				document.removeEventListener("pointerdown", onPointerDown, true);
				document.removeEventListener("pointermove", onPointerMove, true);
				document.removeEventListener("keydown", onKeyDown, true);
				root.remove();
				style.remove();
				if (window.__DTP_ACTIVE__ === api) window.__DTP_ACTIVE__ = undefined;
			}

			const api = { dispose, translate, startCapture };
			window.__DTP_ACTIVE__ = api;
			ctx.effect(() => () => dispose());
			try {
				Promise.resolve(callApi("clientReady", { version: "0.8.1" })).catch(() => {});
				Promise.resolve(callApi("capabilities"))
					.then((value) => {
						if (value && typeof value.target === "string") state.targetDefault = value.target;
						if (value && typeof value.hotkey === "boolean") state.globalHotkey = value.hotkey;
					})
					.catch(() => {});
			} catch {}
			console.info("[translate-pin] client ready");
		}

		function isLightTheme() {
			try {
				const background = getComputedStyle(document.body).backgroundColor;
				const match = background && background.match(/rgba?\(([^)]+)\)/);
				if (!match) return false;
				const parts = match[1].split(",").map((value) => parseFloat(value));
				if (parts.length >= 3) {
					const [r, g, b] = parts;
					return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.55;
				}
			} catch {}
			return false;
		}

		function langName(tag) {
			if (!tag) return "自动";
			const key = String(tag).toLowerCase();
			const table = {
				"zh": "中文", "zh-cn": "简体中文", "zh-hans": "简体中文", "zh-sg": "简体中文",
				"zh-tw": "繁體中文", "zh-hant": "繁體中文", "zh-hk": "繁體中文",
				"en": "英语", "ja": "日语", "ko": "韩语", "fr": "法语", "de": "德语",
				"es": "西班牙语", "ru": "俄语", "it": "意大利语", "pt": "葡萄牙语", "ar": "阿拉伯语",
				"auto": "自动",
			};
			return table[key] || tag;
		}

		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	},
});



















