import { basicSetup, EditorView } from "codemirror";
import { keymap } from "@codemirror/view";
import { EditorState, Compartment } from "@codemirror/state";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { StreamLanguage, indentUnit } from "@codemirror/language";
import { shell } from "@codemirror/legacy-modes/mode/shell";
import { oneDark } from "@codemirror/theme-one-dark";
import { defaultKeymap, indentWithTab } from "@codemirror/commands";

const langs = {
  python: () => python(),
  shell: () => StreamLanguage.define(shell),
  javascript: () => javascript(),
  json: () => json(),
  markdown: () => markdown(),
  none: () => [],
};

function languageFor(name) {
  if (!name) return [];
  if (name.endsWith(".py")) return python();
  if (name.endsWith(".sh") || name.endsWith(".bash")) return StreamLanguage.define(shell);
  if (name.endsWith(".js")) return javascript();
  if (name.endsWith(".json")) return json();
  if (name.endsWith(".md")) return markdown();
  return [];
}

class GumCPEditor {
  constructor(el, opts = {}) {
    this.el = el;
    this.onChange = opts.onChange || null;
    this.readonly = !!opts.readonly;
    this.darkCompartment = new Compartment();
    this.langCompartment = new Compartment();
    this.tabCompartment = new Compartment();

    this.view = new EditorView({
      state: EditorState.create({
        doc: opts.value || "",
        extensions: [
          basicSetup,
          keymap.of([...defaultKeymap, indentWithTab]),
          this.langCompartment.of(languageFor(opts.filename)),
          this.tabCompartment.of(indentUnit.of("    ")),
          this.darkCompartment.of(opts.dark ? oneDark : []),
          EditorView.editable.of(!this.readonly),
          EditorView.updateListener.of((update) => {
            if (update.docChanged && this.onChange) this.onChange(this.getValue());
          }),
          EditorView.theme({
            "&": { height: opts.height || "520px", fontSize: "13px", border: "1px solid var(--bs-border-color)", borderRadius: "6px" },
            ".cm-scroller": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", overflow: "auto" },
            "&.cm-focused": { outline: "none" },
          }),
        ],
      }),
      parent: el,
    });
  }
  getValue() { return this.view.state.doc.toString(); }
  setValue(v) {
    this.view.dispatch({ changes: { from: 0, to: this.view.state.doc.length, insert: v || "" } });
  }
  setFilename(name) {
    this.view.dispatch({ effects: this.langCompartment.reconfigure(languageFor(name)) });
  }
  setDark(dark) {
    this.view.dispatch({ effects: this.darkCompartment.reconfigure(dark ? oneDark : []) });
  }
  focus() { this.view.focus(); }
  destroy() { this.view.destroy(); }
}

window.GumCPEditor = GumCPEditor;
