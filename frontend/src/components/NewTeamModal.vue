<script setup lang="ts">
/* 1:1 port of the prototype NewTeamModal, wired to the real teams API. */
import { computed, reactive, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import ModalShell from "@/components/ModalShell.vue";
import { teamsApi } from "@/api/teams";
import { useChatStore } from "@/stores/chat";
import type { TeamDetail } from "@/types";

const emit = defineEmits<{ close: []; created: [TeamDetail] }>();
const chat = useChatStore();

const COLORS = ["#b8852a", "#3a6da1", "#8a5aa1", "#5b8a4a", "#c45a3a", "#3a8a7a", "#6a3aa1", "#1d1a14"];
const ICONS = ["cube", "sparkle", "target", "chart2", "doc", "globe", "compass", "star"];

const form = reactive({
  name: "", handle: "", color: COLORS[0], icon: ICONS[0], tagline: "",
  plan: "team", sharedAgents: [] as string[], visibility: "invite", emails: "",
});
let handleEdited = false as boolean;
watch(() => form.name, (v) => {
  if (!handleEdited) form.handle = (v || "").trim().toLowerCase().replace(/\s+/g, "-").replace(/[^\w\-一-鿿]/g, "").slice(0, 24);
});
const valid = computed(() => form.name.trim().length >= 2);
const parsedEmails = computed(() => (form.emails || "").split(/[,\s;\n]+/).map((s) => s.trim()).filter((s) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s)));
const busy = ref(false);
function toggleAgent(id: string) {
  const i = form.sharedAgents.indexOf(id);
  i >= 0 ? form.sharedAgents.splice(i, 1) : form.sharedAgents.push(id);
}
async function submit() {
  if (!valid.value || busy.value) return;
  busy.value = true;
  try {
    const team = await teamsApi.create({ name: form.name.trim(), handle: form.handle || undefined, tagline: form.tagline.trim() || "一个新的协作空间", color: form.color });
    if (form.sharedAgents.length) await teamsApi.setSharedAgents(team.id, [...form.sharedAgents]).catch(() => {});
    for (const em of parsedEmails.value) await teamsApi.addMember(team.id, em, "member").catch(() => {});
    await chat.loadTeams();
    emit("created", team);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <ModalShell title="新建团队" subtitle="团队拥有共享的助手、知识库与项目空间" :width="640" @close="$emit('close')">
    <div class="np-identity">
      <div class="team-shield np-preview" :style="{ background: form.color, width: '56px', height: '56px', borderRadius: '14px' }"><Icon :name="form.icon" :size="24" /></div>
      <div class="flex-1-min">
        <div class="np-name-row">
          <input class="np-input np-name" v-model="form.name" placeholder="团队名称，例如：设计组" maxlength="24" autofocus />
          <span class="np-counter">{{ form.name.length }}/24</span>
        </div>
        <div class="np-handle-row">
          <span class="np-handle-at">@</span>
          <input class="np-input np-handle" :value="form.handle" @input="(e) => { handleEdited = true; form.handle = (e.target as HTMLInputElement).value; }" placeholder="handle" maxlength="24" />
          <span class="np-hint">用于团队链接与 @ 引用</span>
        </div>
      </div>
    </div>

    <div class="np-field">
      <label class="np-label">一句话标语 <span class="np-hint">显示在团队主页标题下</span></label>
      <input class="np-input" v-model="form.tagline" placeholder="例如：把每个像素都送到该去的地方" maxlength="40" />
    </div>

    <div class="np-row">
      <div class="np-field">
        <label class="np-label">颜色</label>
        <div class="np-swatches"><button v-for="c in COLORS" :key="c" class="np-swatch" :class="{ active: form.color === c }" :style="{ background: c }" @click="form.color = c"></button></div>
      </div>
      <div class="np-field">
        <label class="np-label">图标</label>
        <div class="np-icon-row"><button v-for="ic in ICONS" :key="ic" class="np-icon-btn" :class="{ active: form.icon === ic }" @click="form.icon = ic"><Icon :name="ic" /></button></div>
      </div>
    </div>

    <div class="np-row">
      <div class="np-field">
        <label class="np-label">套餐</label>
        <div class="np-seg">
          <button :class="{ active: form.plan === 'team' }" @click="form.plan = 'team'"><Icon name="user" /> 团队版 · 20 席</button>
          <button :class="{ active: form.plan === 'business' }" @click="form.plan = 'business'"><Icon name="bolt" /> 商业版 · 不限</button>
        </div>
      </div>
      <div class="np-field">
        <label class="np-label">加入方式</label>
        <div class="np-seg">
          <button :class="{ active: form.visibility === 'invite' }" @click="form.visibility = 'invite'"><Icon name="pin" /> 仅邀请</button>
          <button :class="{ active: form.visibility === 'org' }" @click="form.visibility = 'org'"><Icon name="globe" /> 全员可申请</button>
        </div>
      </div>
    </div>

    <div class="np-field">
      <label class="np-label">共享助手 <span class="np-hint">团队成员都可使用 · 已选 {{ form.sharedAgents.length }}</span></label>
      <div class="np-agents">
        <button v-for="p in chat.profiles.filter((pp) => pp.is_active)" :key="p.id" class="np-agent" :class="{ on: form.sharedAgents.includes(p.id) }" @click="toggleAgent(p.id)">
          <span class="np-agent-ico" :style="{ background: p.color || '#b8852a' }"><Icon :name="p.icon || 'sparkle'" /></span>
          <span class="np-agent-nm">{{ p.name }}</span>
          <span v-if="form.sharedAgents.includes(p.id)" class="np-agent-check"><Icon name="check" :size="9" /></span>
        </button>
      </div>
    </div>

    <div class="np-field">
      <label class="np-label">邀请成员 <span class="np-hint">可选 · 一行一个邮箱{{ parsedEmails.length ? "，已识别 " + parsedEmails.length + " 个" : "" }}</span></label>
      <textarea class="np-input np-textarea" v-model="form.emails" rows="2" placeholder="xubai@hermes.io, yanzhi@hermes.io"></textarea>
    </div>

    <template #foot>
      <span class="np-foot-hint">你将成为团队所有者</span>
      <span class="flex-1"></span>
      <button class="btn" @click="$emit('close')">取消</button>
      <button class="btn primary" :disabled="!valid || busy" @click="submit"><Icon name="plus" /> {{ busy ? "创建中…" : "创建团队" }}</button>
    </template>
  </ModalShell>
</template>
