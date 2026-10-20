-- MindGap: concepts and cases (answer keys written before any session)
-- Run after schema.sql. Safe to re-run.

insert into concepts (id, title, sort_order) values
  ('key_in_browser',    'Why can''t the API key live in the browser?', 1),
  ('app_forgets',       'Why does the app forget without storage?',    2),
  ('backend_exists',    'Why does a backend exist at all?',            3),
  ('history_row',       'Why is history a row, not a cell?',           4),
  ('pointer_many_side', 'Why does the pointer sit on the many side?',  5)
on conflict (id) do update set title = excluded.title, sort_order = excluded.sort_order;

insert into cases (id, concept_id, level, case_text, must_hit, must_not) values

-- ===== Key in the browser =====
('key_1', 'key_in_browser', 'first',
 'Launch day. Your weather app SkyCast has just reached the top of Product Hunt, and thousands of people are opening it. The paid weather API key is written directly in the React code. Walk me through the next month: what happens, and why?',
 '[{"id":"h1","text":"Anyone visiting the site can see the key in their browser (developer tools, network tab or the JavaScript code)"},
   {"id":"h2","text":"Someone copies the key and uses it outside the app, in their own code or app"},
   {"id":"h3","text":"The app owner gets billed for those calls or runs out of quota"}]',
 '[{"id":"n1","text":"HTTPS or encryption hides the key from the user"},
   {"id":"n2","text":"The key simply expires on its own"}]'),

('key_2', 'key_in_browser', 'twin',
 'After launch, the SkyCast team moves the API key so it only appears on a page that signed-in users can open. The founder says: "Sorted, strangers cannot see it now." Do you agree? Walk me through why.',
 '[{"id":"h1","text":"No, it is not safe"},
   {"id":"h2","text":"Any logged-in user still receives the page code, so they can read the key in developer tools and copy it"},
   {"id":"h3","text":"Login only controls who sees the page; it does not protect the key once it reaches their browser"}]',
 '[{"id":"n1","text":"A frontend .env file or environment variable hides the key"},
   {"id":"n2","text":"The app can safely fetch the key from the server when it needs it"}]'),

('key_3', 'key_in_browser', 'after',
 'SkyCast now ships as an Android app. The API key lives inside the app code, and only people who install the app have it. Is the key safe there? Walk me through your reasoning.',
 '[{"id":"h1","text":"No, it is not safe"},
   {"id":"h2","text":"Anyone who installs the app has the app code on their phone and can pull the key out of it"},
   {"id":"h3","text":"They can reuse the key outside the app and the owner pays for it"}]',
 '[{"id":"n1","text":"Compiling the app hides the key"},
   {"id":"n2","text":"The app can safely fetch the key from the server when it needs it"}]'),

-- ===== App forgets without storage =====
('forget_1', 'app_forgets', 'first',
 'Your study-buddy chatbot is a hit in your hostel. It keeps each conversation in a JavaScript variable on the page. Halfway through a long chat, a user presses refresh. What do they see, and why?',
 '[{"id":"h1","text":"The conversation is lost"},
   {"id":"h2","text":"The variable lived in the page memory, and a refresh wipes that memory"},
   {"id":"h3","text":"To keep it, the chat must be saved somewhere that survives (browser storage or a database) and loaded back"}]',
 '[{"id":"n1","text":"The browser keeps it automatically in its cache"},
   {"id":"n2","text":"It was never stored anywhere (it was stored, in memory)"}]'),

('forget_2', 'app_forgets', 'twin',
 'Friday, 6 PM. 412 students have registered for your college fest through your app. The server keeps the list in a variable in memory. At 6:05 you push a small colour fix and the server restarts. What happens to the list, and what, if anything, would you change?',
 '[{"id":"h1","text":"The signup list is gone"},
   {"id":"h2","text":"Server memory is temporary too: a restart wipes the variable"},
   {"id":"h3","text":"Store the signups in a database so they survive restarts"}]',
 '[{"id":"n1","text":"Because it is on the server, it is saved"}]'),

('forget_3', 'app_forgets', 'after',
 'Your notes app runs on a free hosting plan that puts the server to sleep after 15 quiet minutes and wakes it on the next visit. Notes are kept in a Python dictionary on the server. A user writes a note late at night and opens the app the next morning. What do they see, and why?',
 '[{"id":"h1","text":"The note is gone"},
   {"id":"h2","text":"The dictionary lived in the server process memory, which is wiped when the server sleeps and starts again"},
   {"id":"h3","text":"The note must be saved in a database or file that outlives the server process"}]',
 '[{"id":"n1","text":"Because it is on the server, it is saved"},
   {"id":"n2","text":"The browser cache will bring it back"}]'),

-- ===== Why a backend exists =====
('backend_1', 'backend_exists', 'first',
 'Your quiz app for the class test is ready. To keep it fast, answers are checked by JavaScript in the browser, and the correct answers sit inside that code. Forty students open it at 10 AM. Walk me through what could happen.',
 '[{"id":"h1","text":"The student can read the correct answers or change their score, because the code runs on their own machine (developer tools)"},
   {"id":"h2","text":"The checking must happen on a server the student cannot change"}]',
 '[{"id":"n1","text":"Minifying or obfuscating the code fixes it"}]'),

('backend_2', 'backend_exists', 'twin',
 'Festive sale on your online store. To keep checkout quick, the browser calculates the discounted total and sends that number to the payment step. Walk me through what a shopper could do, and what, if anything, you would change.',
 '[{"id":"h1","text":"The user can change the total before it is sent (edit the request or the code)"},
   {"id":"h2","text":"The server must recalculate the price from its own price data instead of trusting the browser"}]',
 '[{"id":"n1","text":"HTTPS prevents the user from changing it"}]'),

('backend_3', 'backend_exists', 'after',
 'Your college is running its student council election on your app. Each browser counts the votes cast on it and sends the final count to the server, which saves whatever arrives. Walk me through what a voter could do, and where the counting should happen.',
 '[{"id":"h1","text":"A voter can send any count they like, because the browser is under their control"},
   {"id":"h2","text":"The server must count the votes itself from its own records and accept only one vote per person"}]',
 '[{"id":"n1","text":"Hiding or minifying the code prevents it"},
   {"id":"n2","text":"HTTPS prevents it"}]'),

-- ===== History is a row =====
('history_1', 'history_row', 'first',
 'Your app has a users table with a plan column (Free or Pro). When someone switches plan, that column is updated. An investor asks: "How many users were on Pro in March?" How do you answer, and why?',
 '[{"id":"h1","text":"No, the old plan values are gone because each change overwrote the cell"},
   {"id":"h2","text":"Each plan change should be stored as its own row with a date"}]',
 '[{"id":"n1","text":"Check the server logs (as the design answer)"},
   {"id":"n2","text":"Add a previous_plan column"}]'),

('history_2', 'history_row', 'twin',
 'Last week you raised the price of your online course from 499 to 599 rupees by updating the price in the products table. A customer writes in, sure that they paid 499 last month. Can you check what they actually paid? Walk me through it.',
 '[{"id":"h1","text":"No, the old price was overwritten and is lost"},
   {"id":"h2","text":"Store the price on each order at the time of purchase, or keep price history as dated rows"}]',
 '[{"id":"n1","text":"Add a previous_price column"}]'),

('history_3', 'history_row', 'after',
 'Your delivery app keeps each user address in an address column, updated whenever they move. A parcel sent in January never arrived, and support asks: "Which address did we ship it to?" Can you answer? Walk me through why.',
 '[{"id":"h1","text":"No, the January address was overwritten"},
   {"id":"h2","text":"Save the shipping address on the order when it is placed, or keep address history as dated rows"}]',
 '[{"id":"n1","text":"Add a previous_address column"}]'),

-- ===== Pointer on the many side =====
('pointer_1', 'pointer_many_side', 'first',
 'Your blogging platform has an authors table with a column post_ids that looks like "1,5,9". Each post has exactly one author. Your top writer is about to publish her 500th post. Walk me through how this design holds up, and where you would keep the link.',
 '[{"id":"h1","text":"A list inside one cell is hard to search, update and keep correct, and it keeps growing"},
   {"id":"h2","text":"Put author_id on each post instead, so each post points to its one author"}]',
 '[{"id":"n1","text":"Just make the column bigger"}]'),

('pointer_2', 'pointer_many_side', 'twin',
 'Your fantasy cricket app has a teams table with a column player_ids = "3,8,15". Each player is on exactly one team. Mid-season, player 8 is traded to another team. Walk me through what has to change, and how you would design it.',
 '[{"id":"h1","text":"Two lists must be edited (remove 8 from one, add it to the other), which is easy to get wrong"},
   {"id":"h2","text":"Put team_id on each player row, so a move changes one value"}]',
 '[{"id":"n1","text":"Just make the column bigger"}]'),

('pointer_3', 'pointer_many_side', 'after',
 'Your food delivery app has customers and orders. One loyal customer has just placed her 1,000th order. Where does the link between a customer and their orders live, and how does that design hold up as orders keep coming?',
 '[{"id":"h1","text":"customer_id goes on each order row"},
   {"id":"h2","text":"Each order points to exactly one customer, so the customer row never grows, even at the 1,000th order"}]',
 '[{"id":"n1","text":"Store a list of order ids on the customer"}]')

on conflict (id) do update set
  concept_id = excluded.concept_id,
  level      = excluded.level,
  case_text  = excluded.case_text,
  must_hit   = excluded.must_hit,
  must_not   = excluded.must_not;
