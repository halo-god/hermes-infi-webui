<script setup lang="ts">
import Icon from "@/components/Icon.vue";
import type { Profile } from "@/api/agents";

defineProps<{
  profile: Profile;
  active?: boolean;
}>();

defineEmits<{
  pick: [profile: Profile];
}>();
</script>

<template>
  <button
    class="menu-item profile-item"
    :class="{ active }"
    @click="$emit('pick', profile)"
  >
    <span class="profile-avatar" :style="{ background: profile.color }">
      <Icon :name="profile.icon" />
    </span>
    <div class="profile-meta">
      <div class="profile-name">
        {{ profile.name }}
        <span class="profile-handle">@{{ profile.handle }}</span>
      </div>
      <div class="profile-desc">{{ profile.desc }}</div>
    </div>
    <span v-if="profile.is_moa" class="profile-tag" title="MoA 混合模型">MoA</span>
    <span v-else class="profile-tag">{{ profile.default_model }}</span>
  </button>
</template>
