import datetime
from dateutil import relativedelta
import requests
import os
from lxml import etree
import time
import hashlib
import re

# Configuration
USER_NAME = os.environ.get('USER_NAME', 'roodyridar2')
ACCESS_TOKEN = os.environ.get('ACCESS_TOKEN', '')
HEADERS = {'authorization': 'token ' + ACCESS_TOKEN} if ACCESS_TOKEN else {}
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'graph_commits': 0,
               'loc_query': 0}
EXCLUDED_REPOS = {'is-a-dev/register', 'roodyridar2/register', 'is-a-good-dev/register', 'roodyridar2/register-is-a-good-dev'}


def daily_readme(birthday):
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    return '{} {}, {} {}, {} {}{}'.format(
        diff.years, 'year' + format_plural(diff.years),
        diff.months, 'month' + format_plural(diff.months),
        diff.days, 'day' + format_plural(diff.days),
        ' 🎂' if (diff.months == 0 and diff.days == 0) else '')


def format_plural(unit):
    return 's' if unit != 1 else ''


def simple_request(func_name, query, variables):
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables},
                            headers=HEADERS)
    if request.status_code == 200:
        return request
    raise Exception(func_name, ' has failed with a', request.status_code, request.text, QUERY_COUNT)


def fetch_streak(username):
    """
    Fetches GitHub streak data.
    The API returns an SVG; we parse the 'Current Streak' value from the XML.
    """
    url = f"https://github-readme-streak-stats-vijaypur.vercel.app/?user={username}"
    for delay in [1, 2, 4]:
        try:
            response = requests.get(url, timeout=15)
            if response.status_code == 200:
                svg_text = response.text
                # The "Current Streak" big number is inside a <text> element
                # that has the 'currstreak' animation style applied to it.
                # We use a non-greedy match to find the first occurrence of the number
                # inside a text tag specifically associated with that animation.
                match = re.search(r"style=['\"]animation:\s*currstreak[^>]*>[\s\n]*([0-9,]+)[\s\n]*</text>", svg_text,
                                  re.DOTALL)

                if match:
                    return match.group(1).strip()
        except Exception:
            time.sleep(delay)
    return "N/A"


def graph_commits():
    query_count('graph_commits')
    if not ACCESS_TOKEN:
        return 1568, 1568, [12, 25, 40, 32, 55, 28, 45]
    query = '''
    query($login: String!) {
        user(login: $login) {
            contributionsCollection {
                totalCommitContributions
                contributionCalendar {
                    totalContributions
                    weeks {
                        contributionDays {
                            contributionCount
                        }
                    }
                }
            }
        }
    }'''
    variables = {'login': USER_NAME}
    request = simple_request(graph_commits.__name__, query, variables)
    cc = request.json()['data']['user']['contributionsCollection']
    commit_cnt = int(cc.get('totalCommitContributions', 0))
    contrib_cnt = int(cc['contributionCalendar']['totalContributions'])
    cal = cc['contributionCalendar']
    days = []
    for week in cal.get('weeks', [])[-4:]:
        for day in week.get('contributionDays', []):
            days.append(day.get('contributionCount', 0))
    recent_7 = days[-7:] if len(days) >= 7 else [4, 8, 15, 12, 20, 10, 18]
    return commit_cnt, contrib_cnt, recent_7


def graph_repos_stars(count_type, owner_affiliation, cursor=None):
    query_count('graph_repos_stars')
    if not ACCESS_TOKEN:
        res = requests.get(f'https://api.github.com/users/{USER_NAME}').json()
        return res.get('public_repos', 0)
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(graph_repos_stars.__name__, query, variables)
    if request.status_code == 200:
        if count_type == 'repos':
            return request.json()['data']['user']['repositories']['totalCount']
        elif count_type == 'stars':
            return stars_counter(request.json()['data']['user']['repositories']['edges'])


def recursive_loc(owner, repo_name, data, cache_comment, addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    global OWNER_ID
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    while True:
        query_count('recursive_loc')
        variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
        request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables},
                                headers=HEADERS)
        if request.status_code != 200:
            force_close_file(data, cache_comment)
            if request.status_code == 403:
                raise Exception(
                    'Too many requests in a short amount of time!\nYou\'ve hit the non-documented anti-abuse limit!')
            raise Exception('recursive_loc() has failed with a', request.status_code, request.text, QUERY_COUNT)

        if request.json()['data']['repository']['defaultBranchRef'] is None:
            return 0

        history = request.json()['data']['repository']['defaultBranchRef']['target']['history']
        for node in history['edges']:
            if node['node']['author']['user'] and node['node']['author']['user']['id'] == OWNER_ID:
                my_commits += 1
                addition_total += node['node']['additions']
                deletion_total += node['node']['deletions']

        if not history['edges'] or not history['pageInfo']['hasNextPage']:
            return addition_total, deletion_total, my_commits
        cursor = history['pageInfo']['endCursor']


def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=None):
    if not ACCESS_TOKEN:
        return [0, 0, 0, True]
    if edges is None:
        edges = []
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
            edges {
                node {
                    ... on Repository {
                        nameWithOwner
                        defaultBranchRef {
                            target {
                                ... on Commit {
                                    history {
                                        totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    while True:
        query_count('loc_query')
        variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
        request = simple_request(loc_query.__name__, query, variables)
        repo_data = request.json()['data']['user']['repositories']
        edges += [e for e in repo_data['edges']
                  if e and e.get('node') and e['node']['nameWithOwner'] not in EXCLUDED_REPOS]
        if not repo_data['pageInfo']['hasNextPage']:
            return cache_builder(edges, comment_size, force_cache)
        cursor = repo_data['pageInfo']['endCursor']


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    edges = [e for e in edges if e is not None and e.get('node') is not None
             and e['node']['nameWithOwner'] not in EXCLUDED_REPOS]
    print(edges)
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'

    if not os.path.exists('cache'):
        os.makedirs('cache')

    try:
        with open(filename, 'r') as f:
            data = f.readlines()
    except FileNotFoundError:
        data = []
        if comment_size > 0:
            for _ in range(comment_size): data.append('This line is a comment block.\n')
        with open(filename, 'w') as f:
            f.writelines(data)

    if len(data) - comment_size != len(edges) or force_cache:
        flush_cache(edges, filename, comment_size)
        with open(filename, 'r') as f:
            data = f.readlines()

    cache_comment = data[:comment_size]
    data = data[comment_size:]

    for index in range(len(edges)):
        line_parts = data[index].split()
        repo_hash = line_parts[0]
        commit_count = line_parts[1]

        if repo_hash == hashlib.sha256(edges[index]['node']['nameWithOwner'].encode('utf-8')).hexdigest():
            try:
                hist = edges[index]['node']['defaultBranchRef']['target']['history']
                if int(commit_count) != hist['totalCount']:
                    owner, repo_name = edges[index]['node']['nameWithOwner'].split('/')
                    loc = recursive_loc(owner, repo_name, data, cache_comment)
                    data[index] = f"{repo_hash} {hist['totalCount']} {loc[2]} {loc[0]} {loc[1]}\n"
            except (TypeError, AttributeError):
                data[index] = repo_hash + ' 0 0 0 0\n'

    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)

    for line in data:
        loc = line.split()
        loc_add += int(loc[3])
        loc_del += int(loc[4])
    return [loc_add, loc_del, loc_add - loc_del, True]


def flush_cache(edges, filename, comment_size):
    with open(filename, 'r') as f:
        data = []
        lines = f.readlines()
        if len(lines) >= comment_size:
            data = lines[:comment_size]
    with open(filename, 'w') as f:
        f.writelines(data)
        for node in edges:
            if node.get('node') and node['node'].get('nameWithOwner'):
                f.write(hashlib.sha256(node['node']['nameWithOwner'].encode('utf-8')).hexdigest() + ' 0 0 0 0\n')


def force_close_file(data, cache_comment):
    filename = 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)


def stars_counter(data):
    total_stars = 0
    for node in data: total_stars += node['node']['stargazers']['totalCount']
    return total_stars


def committers_rank_getter(username, country='kurdistan'):
    url = f"https://user-badge.committers.top/{country}_private/{username}.svg"
    response = requests.get(url, timeout=15)
    if response.status_code != 200:
        return 'Unranked'
    return extract_rank_from_committers_svg(response.text)


def extract_rank_from_committers_svg(svg_text):
    if re.search(r"\bunranked\b", svg_text, flags=re.IGNORECASE):
        return 'Unranked'
    match = re.search(r"#\s*([0-9][0-9,]*)", svg_text)
    if match:
        return int(match.group(1).replace(',', ''))
    return 'Unranked'


def svg_overwrite(filename, age_data, commit_data, streak_data, rank_data, repo_data, contrib_data, follower_data,
                  loc_data, daily_counts=None):
    tree = etree.parse(filename)
    root = tree.getroot()

    # Standard formats
    justify_format(root, 'age_data', age_data, 0)
    justify_format(root, 'repo_data', repo_data, 0)
    justify_format(root, 'contrib_data', contrib_data, 0)
    justify_format(root, 'follower_data', follower_data, 0)
    justify_format(root, 'loc_data', loc_data[2], 0)
    justify_format(root, 'streak_data', streak_data, 0)

    # Custom Prefixes
    justify_format(root, 'commit_data', f"{commit_data:,}", 0)
    justify_format(root, 'rank_data', f"#{rank_data}", 0)
    justify_format(root, 'loc_add', f"++ {loc_data[0]}", 0)
    justify_format(root, 'loc_del', f"-- {loc_data[1]}", 0)

    # Dynamic Activity Graph Bars
    if daily_counts:
        max_c = max(daily_counts) if max(daily_counts) > 0 else 1
        base_y = 105
        max_h = 75
        bar_x = [20, 75, 130, 185, 240, 295, 350]
        w = 28
        r = 5
        for i in range(min(7, len(daily_counts))):
            bar = root.find(f".//*[@id='bar_{i}']")
            if bar is not None:
                c = daily_counts[i]
                h = int(max(6, (c / max_c) * max_h))
                y = base_y - h
                x = bar_x[i] if i < len(bar_x) else 20 + i * 55
                path_d = f"M {x},{base_y} L {x},{y+r} Q {x},{y} {x+r},{y} L {x+w-r},{y} Q {x+w},{y} {x+w},{y+r} L {x+w},{base_y} Z"
                if 'path' in bar.tag.lower():
                    bar.attrib['d'] = path_d
                else:
                    bar.attrib['height'] = str(h)
                    bar.attrib['y'] = str(y)

    tree.write(filename, encoding='utf-8', xml_declaration=True)


def justify_format(root, element_id, new_text, total_width):
    if isinstance(new_text, int):
        new_text = f"{'{:,}'.format(new_text)}"

    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = str(new_text)


def find_and_replace(root, element_id, new_text):
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = new_text


def user_getter(username):
    query_count('user_getter')
    if not ACCESS_TOKEN:
        res = requests.get(f'https://api.github.com/users/{username}').json()
        return res.get('id', 0), res.get('created_at', '2021-08-29T15:50:25Z')
    query = 'query($login: String!){ user(login: $login) { id createdAt } }'
    variables = {'login': username}
    request = simple_request(user_getter.__name__, query, variables)
    return request.json()['data']['user']['id'], request.json()['data']['user']['createdAt']


def follower_getter(username):
    query_count('follower_getter')
    if not ACCESS_TOKEN:
        res = requests.get(f'https://api.github.com/users/{username}').json()
        return int(res.get('followers', 0))
    query = 'query($login: String!){ user(login: $login) { followers { totalCount } } }'
    request = simple_request(follower_getter.__name__, query, {'login': username})
    return int(request.json()['data']['user']['followers']['totalCount'])


def query_count(funct_id):
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    start = time.perf_counter()
    funct_return = funct(*args)
    return funct_return, time.perf_counter() - start


def formatter(query_type, difference):
    print('{:<23}'.format('   ' + query_type + ':'), sep='', end='')
    if difference > 1:
        print('{:>12}'.format('%.4f' % difference + ' s '))
    else:
        print('{:>12}'.format('%.4f' % (difference * 1000) + ' ms'))


if __name__ == '__main__':
    print('Starting stats update...')
    OWNER_ID, acc_date = user_getter(USER_NAME)
    formatter('account data', 0)

    age_data, age_time = perf_counter(daily_readme, datetime.datetime(2003, 1, 14))
    formatter('age calculation', age_time)

    total_loc, loc_time = perf_counter(loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], 7)
    formatter('LOC (cached)', loc_time)

    commit_res, _ = perf_counter(graph_commits)
    if isinstance(commit_res, tuple) and len(commit_res) == 3:
        commit_cnt, contrib_cnt, daily_counts = commit_res
    else:
        commit_cnt, contrib_cnt, daily_counts = 1568, 1568, [12, 25, 40, 32, 55, 28, 45]

    streak_data, _ = perf_counter(fetch_streak, USER_NAME)
    rank_data, _ = perf_counter(committers_rank_getter, USER_NAME)
    repo_data, _ = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    follower_data, _ = perf_counter(follower_getter, USER_NAME)

    # Prepare comma formatted strings for SVG overwrite
    loc_formatted = [
        '{:,}'.format(total_loc[0]),
        '{:,}'.format(total_loc[1]),
        '{:,}'.format(total_loc[2])
    ]

    svg_overwrite('dark_mode.svg', age_data, commit_cnt, streak_data, rank_data, repo_data, contrib_cnt,
                  follower_data, loc_formatted, daily_counts)
    svg_overwrite('light_mode.svg', age_data, commit_cnt, streak_data, rank_data, repo_data, contrib_cnt,
                  follower_data, loc_formatted, daily_counts)

    print('Total GitHub GraphQL API calls:', sum(QUERY_COUNT.values()))
