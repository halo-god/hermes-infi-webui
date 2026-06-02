<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { teamsApi } from "@/api/teams";

const route = useRoute();
const router = useRouter();

const token = route.params.token as string;
const status = ref<"loading" | "success" | "already" | "error">("loading");
const message = ref("");
const teamId = ref("");

onMounted(async () => {
  try {
    const res = await teamsApi.joinByToken(token);
    teamId.value = res.team_id;
    status.value = res.joined ? "success" : "already";
    message.value = res.message;
  } catch (e: any) {
    status.value = "error";
    message.value = e?.response?.data?.detail || "邀请链接无效或已过期";
  }
});

function goToTeam() {
  if (teamId.value) router.push(`/teams/${teamId.value}`);
  else router.push("/");
}
</script>

<template>
  <div style="min-height: 100vh; display: flex; align-items: center; justify-content: center; background: var(--bg-page)">
    <div style="background: var(--bg-panel); border: 1px solid var(--rule); border-radius: 14px; padding: 40px 48px; text-align: center; max-width: 400px; width: 90%">
      <div v-if="status === 'loading'" style="font-size: 15px; color: var(--ink-soft)">正在验证邀请链接…</div>
      <template v-else-if="status === 'success'">
        <div style="font-size: 32px; margin-bottom: 12px">🎉</div>
        <div style="font-size: 18px; font-weight: 600; color: var(--ink); margin-bottom: 8px">加入成功！</div>
        <div style="font-size: 13.5px; color: var(--ink-soft); margin-bottom: 24px">{{ message }}</div>
        <button class="btn primary" style="width: 100%" @click="goToTeam">前往团队主页</button>
      </template>
      <template v-else-if="status === 'already'">
        <div style="font-size: 32px; margin-bottom: 12px">✓</div>
        <div style="font-size: 18px; font-weight: 600; color: var(--ink); margin-bottom: 8px">已是团队成员</div>
        <div style="font-size: 13.5px; color: var(--ink-soft); margin-bottom: 24px">{{ message }}</div>
        <button class="btn primary" style="width: 100%" @click="goToTeam">前往团队主页</button>
      </template>
      <template v-else>
        <div style="font-size: 32px; margin-bottom: 12px">⚠️</div>
        <div style="font-size: 18px; font-weight: 600; color: var(--ink); margin-bottom: 8px">链接无效</div>
        <div style="font-size: 13.5px; color: var(--ink-soft); margin-bottom: 24px">{{ message }}</div>
        <button class="btn" style="width: 100%" @click="router.push('/')">返回首页</button>
      </template>
    </div>
  </div>
</template>
