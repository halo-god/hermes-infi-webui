import { createApp } from "vue";
import { createPinia } from "pinia";
import router from "@/router";
import i18n from "@/i18n";
import App from "@/App.vue";
// Self-hosted latin/latin-ext of Inter + Cormorant Garamond — replaces the
// blocking fonts.googleapis.com <link> (often unreachable from CN). CJK still
// comes from the system stack (PingFang/Noto SC) via tokens.css.
import "@/assets/fonts/fonts.css";
import "@/styles/tokens.css";
import "@/styles/prototype.css"; // 1:1 prototype component styles
import "@/styles/utilities.css"; // shared utility classes

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.use(i18n);

// Global logout signal from the axios refresh interceptor.
window.addEventListener("hermes:logout", () => {
  router.push({ name: "login" });
});

app.mount("#app");
