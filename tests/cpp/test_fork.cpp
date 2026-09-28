// The pid namespace cache across a fork. A process that creates a new pid namespace and then forks
// needs the child to read that namespace again rather than reuse the parent's cached one, or a peer
// could pair a pid from the new namespace with the old namespace's inode and free a slot on a guess.
//
// Creating a pid namespace needs privileges this test cannot assume, so it checks what the fork handler
// controls directly: the child's cache is empty right after fork, which forces the next read to happen
// again. Without the reset, or without the handler registered at all, the child keeps the parent's
// value and this test fails.
#include <doctest/doctest.h>
#include <sharedbox/sharedbox.hpp>

#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>

TEST_CASE("the child of a fork reads its pid, start time and namespace again") {
    const std::uint64_t parent_pidns = sharedbox::detail::current_pidns();
    const std::uint32_t parent_pid = sharedbox::detail::current_pid();
    CHECK(sharedbox::detail::current_start() != 0);
    CHECK((sharedbox::detail::cache().pidns.load() != 0 && sharedbox::detail::cache().start.load() != 0));
    const pid_t child = fork();
    if (child < 0) {
        FAIL("fork failed");
    }
    if (child == 0) {
        const bool reset =
            sharedbox::detail::cache().pidns.load() == 0 && sharedbox::detail::cache().start.load() == 0;
        const bool same_namespace = sharedbox::detail::current_pidns() == parent_pidns;
        const bool own_pid = sharedbox::detail::current_pid() == static_cast<std::uint32_t>(getpid()) &&
                             sharedbox::detail::current_pid() != parent_pid;
        _exit(reset && same_namespace && own_pid ? 0 : 1);
    }
    int code = 0;
    CHECK(waitpid(child, &code, 0) == child);
    CHECK((WIFEXITED(code) && WEXITSTATUS(code) == 0));
}

// process_start opens /proc/<pid>/stat after kill() has already found the process; if that open then
// fails for a reason other than the process being gone (EMFILE here), the result must say "unknown",
// not "no such process". Forking keeps this process's own descriptor limit intact for doctest's report.
TEST_CASE("an open failure other than the process being gone reports an unknown start time") {
    const std::uint32_t parent_pid = sharedbox::detail::current_pid();
    const pid_t child = fork();
    if (child < 0) {
        FAIL("fork failed");
    }
    if (child == 0) {
        rlimit limit{0, 0};
        if (setrlimit(RLIMIT_NOFILE, &limit) != 0)
            _exit(2);
        const std::uint64_t start = sharedbox::detail::process_start(parent_pid);
        _exit(start == sharedbox::detail::start_unknown ? 0 : 1);
    }
    int code = 0;
    CHECK(waitpid(child, &code, 0) == child);
    CHECK((WIFEXITED(code) && WEXITSTATUS(code) == 0));
}
