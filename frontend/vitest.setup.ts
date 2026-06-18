// Global test setup. Raise testing-library's async-utility timeout so that
// `findBy*` / `waitFor` queries don't flake under heavy parallel CPU load
// (the suite runs many jsdom files concurrently; the 1000ms default is too
// tight on a busy machine). Real failures still surface — they just get a
// fairer window to render first.
import { configure } from "@testing-library/dom";

configure({ asyncUtilTimeout: 5000 });
