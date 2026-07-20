-- ─────────────────────────────────────────────────────────────
-- 메일 '단계' 표시용 컬럼 추가 (2026-07-20)
--
-- 왜 필요한가:
--   프로그램은 초안까지만 만들고 발송은 사람이 한다. 그래서
--     팔로업 횟수(mail_followup_count) = 초안을 '만든' 횟수
--     메일 발송 수(mail_sent_count)    = 실제로 '나간' 통수
--   이 둘이 달라야 "1차 팔로업 생성"과 "1차 팔로업 송신"을 구분할 수 있다.
--
-- 실행 방법 (1회만):
--   Supabase 접속 → 왼쪽 메뉴 SQL Editor → 아래 붙여넣고 RUN
--
-- 실행 안 해도 프로그램은 안 죽는다. 다만 대시보드 '메일 단계' 칸이
-- 팔로업 생성/송신을 구분하지 못하고 어림값으로 보인다.
-- ─────────────────────────────────────────────────────────────

ALTER TABLE sellers
  ADD COLUMN IF NOT EXISTS mail_sent_count        integer DEFAULT 0,
  ADD COLUMN IF NOT EXISTS mail_followup1_sent_at text;

-- 이미 발송된 기존 브랜드는 최소 1통 나간 것으로 채워 둔다
-- (다음 자동추적 때 Gmail 실측값으로 교정된다)
UPDATE sellers
   SET mail_sent_count = 1
 WHERE mail_sent_at IS NOT NULL
   AND COALESCE(mail_sent_count, 0) = 0;
