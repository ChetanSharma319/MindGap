-- MindGap: the three questions the tables must answer (no names anywhere)

-- 1. Which concept does this learner keep reciting?
select c.title, count(*) as gaps
from gaps g
join attempts a on a.id = g.attempt_id
join sessions s on s.id = a.session_id
join concepts c on c.id = s.concept_id
where s.learner_id = :learner_id
group by c.title
order by gaps desc;

-- 2. Did the second try pass a new case?  (retry -> gap -> "after" result)
select a.id as retry_id, g.recited_sentence, g.follow_up_question, r.case_id, r.verdict
from attempts a
join gaps g          on g.id = a.gap_id
join case_results r  on r.gap_id = g.id and r.stage = 'after'
where a.type = 'retry';

-- 3. Which sentence do most learners recite?  (grouped by concept and pattern, across learners)
select c.title, g.pattern,
       count(distinct s.learner_id) as learners,
       count(*) as gaps,
       (array_agg(g.recited_sentence order by g.created_at desc))[1:3] as examples
from gaps g
join attempts a on a.id = g.attempt_id
join sessions s on s.id = a.session_id
join concepts c on c.id = s.concept_id
group by c.title, g.pattern
order by learners desc, gaps desc;

-- Owner view: the latest feedback from visitors.
select created_at, rating, source, message
from feedback
order by created_at desc
limit 100;
