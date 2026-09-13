<template>
  <div class="ui-loading" role="status" aria-busy="true" aria-label="正在加载页面数据">
    <div class="ui-loading__line"></div>
    <div class="ui-loading__grid">
      <div v-for="n in 3" :key="n" class="ui-loading__card">
        <div></div>
        <div></div>
      </div>
    </div>
    <span class="sr-only">正在加载，请稍候</span>
  </div>
</template>
<style scoped>
.ui-loading {
  padding: 16px 0;
}
.ui-loading__line {
  width: 180px;
  height: 14px;
  margin-bottom: 24px;
  background: var(--bg-badge);
  border-radius: 5px;
}
.ui-loading__grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.ui-loading__card {
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: 12px;
  padding: 24px;
}
.ui-loading__card div {
  background: var(--bg-badge);
  border-radius: 5px;
  height: 14px;
  width: 60%;
}
.ui-loading__card div + div {
  height: 32px;
  margin-top: 24px;
  width: 80%;
}
@media (max-width: 640px) {
  .ui-loading__grid {
    grid-template-columns: 1fr;
  }
  .ui-loading__card:not(:first-child) {
    display: none;
  }
}
</style>
