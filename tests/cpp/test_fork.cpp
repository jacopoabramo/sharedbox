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

#include <sys/wait.h>
#include <unistd.h>

TEST_CASE("the child of a fork reads its pid, start time and namespace again") {
    const std::uint64_t parent_pidns = sharedbox::current_pidns();
    const std::uint32_t parent_pid = sharedbox::current_pid();
    CHECK(sharedbox::current_start() != 0);
    CHECK((sharedbox::detail::cache().pidns.load() != 0 && sharedbox::detail::cache().start.load() != 0));
    const pid_t child = fork();
    if (child < 0) {
        FAIL("fork failed");
    }
    if (child == 0) {
        const bool reset =
            sharedbox::detail::cache().pidns.load() == 0 && sharedbox::detail::cache().start.load() == 0;
        const bool same_namespace = sharedbox::current_pidns() == parent_pidns;
        const bool own_pid = sharedbox::current_pid() == static_cast<std::uint32_t>(getpid()) &&
                             sharedbox::current_pid() != parent_pid;
        _exit(reset && same_namespace && own_pid ? 0 : 1);
    }
    int code = 0;
    CHECK(waitpid(child, &code, 0) == child);
    CHECK((WIFEXITED(code) && WEXITSTATUS(code) == 0));
}
