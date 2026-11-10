// Safe DOM construction. Strings always become text nodes, never HTML, so user- and
// model-provided text cannot inject markup.

/**
 * @typedef {Record<string, string | number | boolean | null | undefined | EventListener>} Attrs
 * @typedef {Node | string | number | null | undefined | false} Child
 */

/**
 * Create an element. Attribute keys starting with "on" attach event listeners;
 * `class` sets className; boolean true sets an empty attribute; null/false skip.
 * @param {string} tag
 * @param {Attrs} [attrs]
 * @param {...(Child | Child[])} children
 * @returns {HTMLElement}
 */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), /** @type {EventListener} */ (value));
    } else if (key === "class") {
      el.className = String(value);
    } else {
      el.setAttribute(key, value === true ? "" : String(value));
    }
  }
  append(el, children);
  return el;
}

/**
 * @param {HTMLElement} el
 * @param {Array<Child | Child[]>} children
 */
function append(el, children) {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/**
 * Replace all children of `el`.
 * @param {Element} el
 * @param {...(Child | Child[])} children
 */
export function replace(el, ...children) {
  el.replaceChildren();
  append(/** @type {HTMLElement} */ (el), children);
}

/**
 * Query a required element; throws early with a clear message if the template changed.
 * @template {Element} T
 * @param {string} selector
 * @param {ParentNode} [root]
 * @returns {T}
 */
export function $(selector, root = document) {
  const el = root.querySelector(selector);
  if (!el) throw new Error(`Missing element: ${selector}`);
  return /** @type {T} */ (el);
}

/**
 * Debounce a function.
 * @template {(...args: any[]) => void} F
 * @param {F} fn
 * @param {number} wait
 * @returns {F}
 */
export function debounce(fn, wait) {
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let timer;
  return /** @type {F} */ ((...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  });
}
