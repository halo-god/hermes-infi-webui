<script setup lang="ts">
/* 帮助中心 · 知识库管理 - 目录绑定、团队知识、项目文档、Office 预览。 */
import Icon from "@/components/Icon.vue";

const FORMATS = [
  { ext: "docx", desc: "Word 文档，提取正文+表格+嵌入图片" },
  { ext: "xlsx", desc: "Excel 表格，按行列渲染为 HTML 表格" },
  { ext: "pptx", desc: "PPT 幻灯片，按页提取文字和图片" },
  { ext: "pdf", desc: "PDF 文档，提取纯文本" },
  { ext: "csv", desc: "CSV 文件，渲染为表格预览" },
  { ext: "md/txt", desc: "Markdown/纯文本，直接渲染" },
  { ext: "图片", desc: "PNG/JPG/GIF/WebP/SVG，直接显示" },
];
</script>

<template>
  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="folder" /> 知识库目录绑定</div></div>
    <div class="help-intro">
      Profile 助手可以绑定知识库，让 AI 在回答时自动参考你的资料。支持三种绑定方式：直接绑定单条知识、按目录树绑定整个文件夹、绑定整个团队的知识库。
    </div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="doc" :size="14" /> 单条知识绑定</div>
        <div class="help-feature-body">在 Profile 编辑页的"知识库"区域，逐条选择要绑定的知识文件。适合精确引用少量文档的场景。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="folder" :size="14" /> 目录树绑定</div>
        <div class="help-feature-body">选择一个知识目录后，该目录下所有文件（含子目录）都会被递归绑定。新增文件到该目录后自动生效，无需重新配置。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="users" :size="14" /> 整团队绑定</div>
        <div class="help-feature-body">绑定整个团队的知识库后，助手能参考该团队下所有非文件夹类型的知识条目。适合"团队专属助手"场景。</div>
      </div>
    </div>
    <div class="help-tip"><Icon name="sparkle" :size="14" style="flex-shrink:0;margin-top:1px;color:var(--accent-deep)" /> 三种绑定方式可组合使用，系统会自动去重。知识内容在注入 AI prompt 前会从 HTML 转为纯文本，避免标签浪费 token。</div>
  </div>

  <div class="section-card" style="margin-bottom: 16px">
    <div class="section-head"><div class="section-title"><Icon name="doc" /> 团队知识 vs 项目文档</div></div>
    <div class="help-feature-grid">
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="users" :size="14" /> 团队知识库</div>
        <div class="help-feature-body">在团队详情页上传的知识文件，归属整个团队。支持文件夹分组管理、版本历史。可绑定到团队内任意 Profile。</div>
      </div>
      <div class="help-feature">
        <div class="help-feature-title"><Icon name="cube" :size="14" /> 项目文档</div>
        <div class="help-feature-body">在项目详情页上传的文档，归属特定项目。同样支持文件夹和版本管理。可从对话中一键"沉淀为项目文档"。</div>
      </div>
    </div>
  </div>

  <div class="section-card">
    <div class="section-head"><div class="section-title"><Icon name="doc" :size="14" /> Office 文档预览</div></div>
    <div class="help-intro">上传的 Office 文档会自动提取内容并生成 HTML 预览，无需下载即可在浏览器中查看。</div>
    <div class="help-feature-grid">
      <div v-for="f in FORMATS" :key="f.ext" class="help-feature">
        <div class="help-feature-title"><span class="help-kbd">{{ f.ext }}</span></div>
        <div class="help-feature-body">{{ f.desc }}</div>
      </div>
    </div>
    <div class="help-tip"><Icon name="help" :size="14" style="flex-shrink:0;margin-top:1px;color:var(--accent-deep)" /> 大文件（&gt;30KB）不会全文注入 AI prompt，而是生成引用路径，助手通过 read_file 工具按需读取，避免上下文溢出。</div>
  </div>
</template>
