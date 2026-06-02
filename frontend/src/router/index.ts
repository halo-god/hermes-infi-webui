import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/login",
      name: "login",
      component: () => import("@/views/LoginView.vue"),
      meta: { public: true },
    },
    {
      // Persistent shell (sidebar + main); all main screens render inside it.
      path: "/",
      component: () => import("@/views/AppLayout.vue"),
      children: [
        { path: "", name: "home", component: () => import("@/views/ChatView.vue") },
        { path: "history", name: "history", component: () => import("@/views/HistoryView.vue") },
        { path: "teams/:id", name: "team", component: () => import("@/views/TeamDetailView.vue") },
        { path: "projects/:id", name: "project", component: () => import("@/views/ProjectView.vue") },
        { path: "settings", name: "settings", component: () => import("@/views/ProfileView.vue") },
        { path: "schedule", name: "schedule", component: () => import("@/views/ScheduledView.vue") },
        {
          path: "admin",
          name: "admin",
          component: () => import("@/views/AdminView.vue"),
          meta: { requiresAdmin: true },
        },
      ],
    },
    {
      path: "/i/:teamHandle/:token",
      name: "join-team",
      component: () => import("@/views/JoinTeamView.vue"),
      meta: { public: false },
    },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (!auth.ready) await auth.bootstrap();

  if (!to.meta.public && !auth.isAuthenticated) {
    return { name: "login", query: { redirect: to.fullPath } };
  }
  if (to.name === "login" && auth.isAuthenticated) {
    return { name: "home" };
  }
  if (to.meta.requiresAdmin && !auth.isAdmin) {
    return { name: "home" };
  }
  return true;
});

export default router;
