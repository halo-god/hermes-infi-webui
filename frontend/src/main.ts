import { createApp } from "vue";
import { createPinia } from "pinia";
import router from "@/router";
import i18n from "@/i18n";
import App from "@/App.vue";
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
